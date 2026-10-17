"""Load the Open Food Facts snapshot into the database as the product catalog.

    ./dev catalog              # = ./dev py scripts/build_catalog.py
    ./dev catalog --dry-run    # derive everything, print the counts, write nothing

Identity is real Open Food Facts data: English name, the name in the label's own language,
brand, category, pack size, labels, and whether OFF marks the product obsolete.
`fetch_off_products.py` pulls it. The wholesale half of a product — case pack, case price,
MOQ, pallet pattern, case weight, and whether it is sold as 正品 / 赠品 / 样品 — has no public
source, so it is derived here from the real pack size and category. Every derived value is
a pure function of the barcode, so a rebuild gives the same numbers and a product keeps
them when others come and go.

The numbers stay consistent with each other on purpose (case price = unit price x pack,
pallet = layers x cases/layer, weight follows net content): the PO check does arithmetic on
them, and a catalog that contradicts itself would test the data, not the code.

Nothing here invents a fact the source doesn't carry: `restricted_territories` stays empty
(OFF has no such data; market bans live in the rules table).
"""

from __future__ import annotations

import argparse
import hashlib
import math
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterator
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from partnerdesk.db import Database  # noqa: E402

SNAPSHOT = ROOT / "data" / "off" / "products.parquet"

# First match wins, so specific families come before the broad ones that contain them.
# (OFF tag, category, SKU prefix, USD per kg or L at wholesale, storage)
CATEGORIES: list[tuple[str, str, str, float, str]] = [
    ("en:dietary-supplements", "Supplements", "SUP", 40.0, "ambient"),
    ("en:baby-foods", "Baby Food", "BAB", 18.0, "ambient"),
    ("en:alcoholic-beverages", "Alcoholic Beverages", "ALC", 9.0, "ambient"),
    ("en:teas", "Tea", "TEA", 30.0, "ambient"),
    ("en:coffees", "Coffee", "COF", 22.0, "ambient"),
    ("en:frozen-foods", "Frozen Foods", "FRZ", 7.0, "frozen"),
    ("en:dairies", "Dairy", "DAI", 5.0, "chilled"),
    ("en:meats", "Meat", "MEA", 12.0, "chilled"),
    ("en:seafood", "Seafood", "SEA", 14.0, "chilled"),
    ("en:instant-noodles", "Instant Noodles", "NDL", 5.5, "ambient"),
    ("en:noodles", "Noodles & Pasta", "NDL", 4.0, "ambient"),
    ("en:pastas", "Noodles & Pasta", "NDL", 4.0, "ambient"),
    ("en:condiments", "Condiments & Sauces", "CND", 6.0, "ambient"),
    ("en:sauces", "Condiments & Sauces", "CND", 6.0, "ambient"),
    ("en:fats", "Oils & Fats", "OIL", 5.0, "ambient"),
    ("en:spreads", "Spreads", "SPR", 8.0, "ambient"),
    ("en:chocolates", "Chocolate", "CHO", 16.0, "ambient"),
    ("en:confectioneries", "Confectionery", "CNF", 12.0, "ambient"),
    ("en:biscuits-and-cakes", "Biscuits & Cakes", "BSC", 8.0, "ambient"),
    ("en:salty-snacks", "Salty Snacks", "SNK", 10.0, "ambient"),
    ("en:snacks", "Snacks", "SNK", 10.0, "ambient"),
    ("en:breakfasts", "Breakfast", "BRK", 7.0, "ambient"),
    ("en:canned-foods", "Canned Foods", "CAN", 4.5, "ambient"),
    ("en:beverages", "Beverages", "BEV", 2.2, "ambient"),
    ("en:cereals-and-potatoes", "Grains & Cereals", "GRN", 3.0, "ambient"),
    ("en:plant-based-foods", "Plant-based Foods", "PLT", 5.0, "ambient"),
]
DEFAULT_CATEGORY = ("Grocery", "GRC", 6.0, "ambient")

# OFF label tag -> policy tag the rules engine can scope on.
LABEL_TAGS = {
    "en:organic": "organic", "en:vegan": "vegan", "en:vegetarian": "vegetarian", "en:halal": "halal",
    "en:kosher": "kosher", "en:no-gluten": "gluten-free", "en:no-added-sugar": "no-added-sugar",
    "en:no-preservatives": "no-preservatives", "en:fair-trade": "fair-trade",
}

UNIT_TO_BASE = {"g": 1.0, "kg": 1000.0, "mg": 0.001, "ml": 1.0, "cl": 10.0, "dl": 100.0, "l": 1000.0}
QTY_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kg|mg|g|ml|cl|dl|l)\b", re.I)
MULTI_RE = re.compile(r"(\d+)\s*[x×*]\s*(\d+(?:[.,]\d+)?)\s*(kg|mg|g|ml|cl|dl|l)\b", re.I)
NAME_MAX, ID_MAX = 200, 64  # the column widths in orm.py


def h(code: str, salt: str) -> float:
    """Deterministic uniform [0, 1) per product and purpose."""
    d = hashlib.sha256(f"{code}:{salt}".encode()).digest()
    return int.from_bytes(d[:8], "big") / 2**64


def pick(code: str, salt: str, options: list):
    return options[int(h(code, salt) * len(options))]


def pretty_tag(tag: str) -> str:
    s = tag.split(":", 1)[-1].replace("-", " ").strip()
    return s[:1].upper() + s[1:]


def is_latin(text: str) -> bool:
    # OFF's `en` field sometimes holds the label's own name (Cyrillic, Arabic, CJK …); that
    # is not an English name, and the catalog would be lying to call it one.
    return all(unicodedata.name(ch, "").startswith("LATIN") for ch in text if ch.isalpha())


def brand_id(tag: str) -> str:
    # Own namespace, so a catalog brand never shares an id with one created in the app.
    bid = f"off-{tag}"
    if len(bid) <= ID_MAX:
        return bid
    return f"{bid[:ID_MAX - 9]}-{hashlib.sha1(tag.encode()).hexdigest()[:8]}"


def parse_quantity(row: dict) -> tuple[int, float, str] | None:
    """(units per retail pack, amount per unit in g/ml, 'g' | 'ml'); None when unusable."""
    q = row.get("quantity") or ""
    m = MULTI_RE.search(q)
    if m:
        count, amount, unit = int(m[1]), float(m[2].replace(",", ".")), m[3].lower()
    else:
        m = QTY_RE.search(q)
        if m:
            count, amount, unit = 1, float(m[1].replace(",", ".")), m[2].lower()
        else:
            try:
                count, amount, unit = 1, float(row.get("product_quantity") or ""), (row.get("product_quantity_unit") or "g").lower()
            except ValueError:
                return None
    if unit not in UNIT_TO_BASE or count <= 0 or count > 48:
        return None
    base = amount * UNIT_TO_BASE[unit]
    if not 1 <= base * count <= 25_000:  # drop obvious typos: 0.2 g of noodles, 400 kg of tea
        return None
    return count, base, "ml" if unit in ("ml", "cl", "dl", "l") else "g"


def fmt_amount(base: float, dim: str) -> str:
    # US labels convert from ounces ("680.389 g"); a case sheet would print 680 g.
    if base >= 1000:
        return f"{round(base / 1000, 2):g} {'L' if dim == 'ml' else 'kg'}"
    return f"{round(base) if base >= 10 else round(base, 1):g} {dim}"


def classify(tags: list[str]) -> tuple[str, str, float, str]:
    tagset = set(tags)
    for tag, cat, prefix, per_kg, storage in CATEGORIES:
        if tag in tagset:
            return cat, prefix, per_kg, storage
    return DEFAULT_CATEGORY


def case_pack_for(total: float) -> int:
    # Retail packs per case shrink as the pack grows — a carton stays hand-liftable.
    for limit, pack in ((60, 48), (150, 24), (400, 20), (800, 12), (1600, 8), (3000, 6)):
        if total <= limit:
            return pack
    return 4


def status_for(code: str, obsolete: bool) -> str:
    # OFF's own obsolete flag is real; the rest of 停产 and all of 赠品 / 样品 is derived.
    if obsolete or h(code, "disc") < 0.03:
        return "停产"
    r = h(code, "role")
    return "赠品" if r < 0.03 else "样品" if r < 0.06 else "正品"


def product(row: dict, dropped: Counter) -> dict | None:
    code, en, native = row["code"] or "", row["name_en"], row["name_native"]
    if not code or len(f"p-{code}") > ID_MAX:
        dropped["no usable barcode"] += 1
        return None
    if len(en) > NAME_MAX or len(native) > NAME_MAX:
        dropped["name longer than the column"] += 1
        return None
    if not is_latin(en):
        dropped["English name not in Latin script"] += 1
        return None
    qty = parse_quantity(row)
    if qty is None:
        dropped["no usable pack size"] += 1
        return None
    count, per_unit, dim = qty
    tags = list(row["categories_tags"] or [])
    category, prefix, per_kg, storage = classify(tags)
    en_tags = [t for t in tags if t.startswith("en:")]
    subcategory = pretty_tag(en_tags[-1]) if en_tags else category
    total = per_unit * count  # g or ml in one retail pack

    # Price per retail pack: category rate with volume discount on big packs, +/-25% spread.
    unit_price = max(0.35, per_kg * (total / 1000) ** 0.85 * (0.75 + 0.5 * h(code, "price")))
    case_pack = case_pack_for(total)
    case_weight = round(total / 1000 * case_pack * 1.15 + 0.35, 1)  # water density is close enough for a carton
    cases_per_layer = next(n for w, n in ((3, 16), (6, 12), (10, 10), (15, 8), (1e9, 6)) if case_weight < w)
    layers_per_pallet = max(3, min(8, math.floor(900 / (cases_per_layer * case_weight))))
    # MOQ is usually whole cases; every tenth product has an odd one so the ceil path runs.
    moq_units = case_pack * pick(code, "moq", [2, 3, 5, 5, 10, 10, 20]) + (case_pack // 2 if h(code, "moq-odd") < 0.1 else 0)

    labels = set(row["labels_tags"] or [])
    policy = [storage, *sorted({v for k, v in LABEL_TAGS.items() if k in labels})]
    if category == "Alcoholic Beverages":
        policy.append("alcohol")
    if any("pork" in t for t in tags):
        policy.append("pork")
    if category == "Baby Food":
        policy.append("infant-nutrition")

    form = (
        "Frozen" if storage == "frozen"
        else "Sauce" if prefix == "CND"
        else "Spread" if prefix == "SPR"
        else "Liquid" if dim == "ml"
        else "Powder" if any("powder" in t for t in tags)
        else "Dried" if prefix in ("NDL", "TEA")
        else "Solid"
    )
    unit_size = f"{count} x {fmt_amount(per_unit, dim)}" if count > 1 else fmt_amount(per_unit, dim)
    description = f"{subcategory} by {row['brand']}, {unit_size} per retail pack."
    generic = row["generic_name_en"]
    if generic and generic.casefold() != en.casefold():
        description = f"{generic}. {description}"
    description += f" Barcode {code} (Open Food Facts)."

    return {
        "id": f"p-{code}",
        "brand_id": brand_id(row["brand_tag"]),
        "sku": f"{prefix}-{code}",  # the barcode keeps it unique and stable across refreshes
        "product_name_en": en,
        "product_name_native": native,
        "category": category,
        "subcategory": subcategory[:100],
        "variant": f"{count}-pack" if count > 1 else "Single",
        "form": form,
        "unit_size": unit_size,
        "net_content": fmt_amount(total, dim),
        "description_en": description,
        "case_pack": case_pack,
        "case_price": round(unit_price * case_pack, 2),
        "moq_units": moq_units,
        "cases_per_layer": cases_per_layer,
        "layers_per_pallet": layers_per_pallet,
        "cases_per_pallet": cases_per_layer * layers_per_pallet,
        "case_weight": case_weight,
        "product_status": status_for(code, row["obsolete"]),
        "policy_tags": policy,
        "restricted_territories": [],
    }


def rows(columns: list[str] | None = None) -> Iterator[dict]:
    for batch in pq.ParquetFile(SNAPSHOT).iter_batches(batch_size=50_000, columns=columns):
        yield from batch.to_pylist()


def brands() -> list[dict]:
    """One brand per OFF brand tag, named by the spelling its products use most."""
    spellings: dict[str, Counter] = defaultdict(Counter)
    for r in rows(["brand", "brand_tag"]):
        spellings[r["brand_tag"]][r["brand"]] += 1
    return [{"id": brand_id(tag), "name": names.most_common(1)[0][0][:NAME_MAX]} for tag, names in spellings.items()]


def products(dropped: Counter, stats: Counter) -> Iterator[dict]:
    seen: set[str] = set()
    for r in rows():
        if r["code"] in seen:
            dropped["duplicate barcode"] += 1
            continue
        seen.add(r["code"])
        p = product(r, dropped)
        if p:
            stats[p["product_status"]] += 1
            stats[p["category"]] += 1
            yield p


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="derive everything and print the counts; write nothing")
    args = ap.parse_args()
    if not SNAPSHOT.is_file():
        print(f"{SNAPSHOT} is missing — run scripts/fetch_off_products.py first.", file=sys.stderr)
        return 1

    t0 = time.time()
    dropped: Counter = Counter()
    stats: Counter = Counter()
    b = brands()
    if args.dry_run:
        n = sum(1 for _ in products(dropped, stats))
        print(f"{len(b)} brands, {n} products (dry run, nothing written)")
    else:
        result = Database().replace_catalog(b, products(dropped, stats))
        print(f"{result['brands']} brands, {result['products']} products loaded; "
              f"{result['removed']} products no longer in the catalog removed ({time.time() - t0:.0f}s)")
    for reason, n in dropped.most_common():
        print(f"  dropped {n:7d}  {reason}")
    for key, n in stats.most_common():
        print(f"  {n:7d}  {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
