"""Snapshot the Open Food Facts products the catalog can use into `data/off/products.parquet`.

    ./dev py scripts/fetch_off_products.py

Kept: an English name, a name in the label's own language (for English labels that is the
same name), a brand, and some pack-size text. Everything else in the 4.75 M-row export is
dropped here, so `build_catalog.py` reads ~0.5 M rows instead of the whole file.

The source is the 7.9 GB parquet export on Hugging Face (openfoodfacts/product-database).
Reading it through a generic HTTP filesystem gets rate-limited (HTTP 429) into a stall, so
this reads the footer, then fetches only the needed column chunks, a batch of row groups at
a time, in parallel. About half an hour; the output is gitignored and only needs refetching
to refresh the data.
Data: Open Food Facts contributors, ODbL 1.0 — https://world.openfoodfacts.org/data
"""

from __future__ import annotations

import bisect
import io
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "off" / "products.parquet"
URL = "https://huggingface.co/datasets/openfoodfacts/product-database/resolve/main/food.parquet"
COLUMNS = [
    "code", "lang", "product_name", "generic_name", "brands", "brands_tags", "quantity",
    "product_quantity", "product_quantity_unit", "categories_tags", "labels_tags", "obsolete",
]
OUT_SCHEMA = pa.schema([
    ("code", pa.string()), ("lang", pa.string()), ("name_en", pa.string()), ("name_native", pa.string()),
    ("generic_name_en", pa.string()), ("brand", pa.string()), ("brand_tag", pa.string()),
    ("quantity", pa.string()), ("product_quantity", pa.string()), ("product_quantity_unit", pa.string()),
    ("categories_tags", pa.list_(pa.string())), ("labels_tags", pa.list_(pa.string())), ("obsolete", pa.bool_()),
])
BATCH = 150  # row groups per prefetch; bounds memory to a few hundred MB
WORKERS = 16


class RangeReader(io.RawIOBase):
    """A seekable file over HTTP range requests. Chunks are prefetched in bulk; anything else
    pyarrow asks for (page headers it reads ahead of a chunk) is fetched on demand."""

    def __init__(self) -> None:
        self.pos = 0
        self.cache: dict[int, bytes] = {}
        self.starts: list[int] = []  # sorted keys of cache
        self._local = threading.local()
        self._cdn: tuple[str, float] | None = None
        self._lock = threading.Lock()
        head = httpx.head(URL, follow_redirects=True, timeout=60)
        self.size = int(head.headers["content-length"])
        trailer = self.get(self.size - 8, self.size - 1)
        footer_len = struct.unpack("<I", trailer[:4])[0]
        self.footer_start = self.size - 8 - footer_len
        self.footer = self.get(self.footer_start, self.size - 1)

    def _url(self) -> str:
        # The resolve URL redirects to a signed CDN URL; going there directly saves a round trip per chunk.
        with self._lock:
            if self._cdn is None or time.time() - self._cdn[1] > 600:
                r = httpx.get(URL, headers={"Range": "bytes=0-0"}, follow_redirects=False, timeout=60)
                self._cdn = (r.headers["location"], time.time())
            return self._cdn[0]

    def get(self, a: int, b: int) -> bytes:
        for attempt in range(40):
            try:
                if not hasattr(self._local, "client"):
                    self._local.client = httpx.Client(timeout=60, follow_redirects=True)
                r = self._local.client.get(self._url(), headers={"Range": f"bytes={a}-{b}"})
                if r.status_code == 403:  # signed URL expired
                    self._cdn = None
                r.raise_for_status()
                return r.content
            except (httpx.HTTPError, OSError) as e:
                wait = min(60, 3 * (attempt + 1))
                print(f"  retrying bytes {a}-{b} in {wait}s: {e}", flush=True)
                time.sleep(wait)
        raise RuntimeError(f"gave up on bytes {a}-{b}")

    def prefetch(self, ranges: list[tuple[int, int]]) -> None:
        merged: list[list[int]] = []
        for a, b in sorted(ranges):
            if merged and a <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        # A merged range can start where an earlier, shorter blob starts (the second pass's
        # columns sit right after the name chunks) — only full coverage counts as cached.
        todo = [(a, b) for a, b in merged if self._cached(a, b + 1) is None]
        with ThreadPoolExecutor(WORKERS) as ex:
            for (a, _), data in zip(todo, ex.map(lambda r: self.get(*r), todo), strict=True):
                self._put(a, data)

    def _put(self, start: int, data: bytes) -> None:
        if len(data) > len(self.cache.get(start, b"")):
            if start not in self.cache:
                bisect.insort(self.starts, start)
            self.cache[start] = data

    def _cached(self, a: int, b: int) -> bytes | None:
        i = bisect.bisect_right(self.starts, a)
        # Blobs overlap after merging, so look back a few for one that covers [a, b).
        for s in reversed(self.starts[max(0, i - 4):i]):
            blob = self.cache[s]
            if b <= s + len(blob):
                return blob[a - s:b - s]
        return None

    def drop_cache(self) -> None:
        self.cache.clear()
        self.starts.clear()

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        self.pos = offset if whence == 0 else self.pos + offset if whence == 1 else self.size + offset
        return self.pos

    def read(self, n: int = -1) -> bytes:
        a = self.pos
        b = self.size if n < 0 else min(self.size, a + n)
        if a >= self.footer_start:
            data = self.footer[a - self.footer_start:b - self.footer_start]
        else:
            data = self._cached(a, b)
            if data is None:
                blob = self.get(a, max(b, a + (1 << 18)) - 1)
                self._put(a, blob)
                data = blob[:b - a]
        self.pos += len(data)
        return data


def clean(text: str | None) -> str:
    return " ".join((text or "").split())


def keep(row: dict) -> dict | None:
    """The catalog row, or None when the product lacks something the catalog needs."""
    names = {n["lang"]: clean(n["text"]) for n in row["product_name"] or [] if clean(n["text"])}
    lang = row["lang"] or ""
    native = names.get(lang) or names.get("main", "")
    en = names.get("en") or (native if lang == "en" else "")
    tags = row["brands_tags"] or []
    if not (en and native and tags and (clean(row["quantity"]) or clean(row["product_quantity"]))):
        return None
    generic = {n["lang"]: clean(n["text"]) for n in row["generic_name"] or [] if clean(n["text"])}
    return {
        "code": row["code"], "lang": lang, "name_en": en, "name_native": native, "generic_name_en": generic.get("en", ""),
        # `brands` is free text and `brands_tags` its normalised form, in the same order.
        "brand": clean((row["brands"] or "").split(",")[0]) or tags[0], "brand_tag": tags[0],
        "quantity": clean(row["quantity"]), "product_quantity": clean(row["product_quantity"]),
        "product_quantity_unit": clean(row["product_quantity_unit"]),
        "categories_tags": row["categories_tags"] or [], "labels_tags": row["labels_tags"] or [],
        "obsolete": bool(row["obsolete"]),
    }


def main() -> int:
    t0 = time.time()
    f = RangeReader()
    pf = pq.ParquetFile(f, pre_buffer=False)
    md = pf.metadata
    paths = [md.schema.column(i).path for i in range(md.num_columns)]

    def chunk_ranges(rg: int) -> list[tuple[int, int]]:
        out = []
        for i, path in enumerate(paths):
            if any(path == c or path.startswith(c + ".") for c in COLUMNS):
                col = md.row_group(rg).column(i)
                # Some writers leave has_dictionary_page unset although a dictionary page precedes the data.
                start = min(x for x in (col.dictionary_page_offset, col.data_page_offset) if x)
                out.append((start, start + col.total_compressed_size - 1))
        return out

    n = md.num_row_groups
    print(f"{md.num_rows} products in {n} row groups", flush=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".part")
    kept = 0
    with pq.ParquetWriter(tmp, OUT_SCHEMA, compression="zstd") as writer:
        for start in range(0, n, BATCH):
            groups = range(start, min(n, start + BATCH))
            f.prefetch([r for i in groups for r in chunk_ranges(i)])
            rows = [k for i in groups for r in pf.read_row_group(i, columns=COLUMNS).to_pylist() if (k := keep(r))]
            if rows:
                writer.write_table(pa.Table.from_pylist(rows, schema=OUT_SCHEMA))
            kept += len(rows)
            f.drop_cache()
            print(f"  row groups {groups.stop}/{n}: kept {kept}, {time.time() - t0:.0f}s", flush=True)
    tmp.replace(OUT)
    print(f"wrote {kept} rows to {OUT.relative_to(ROOT)} in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
