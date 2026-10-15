"""Read an agreement file into the extraction `contracts.ingest_extraction` already stores.

The model fills the fixed form (dates, tiers, MOQs, territories, verbatim quotes). It does
not derive PO rules and it does not decide that a term applies — `contracts.derive_rules`
does that from the normalized rows. A quote that is not actually in the text is dropped.
Scanned pages (no text layer) go along as images, the same way an order photo does.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from typing import Any

from .contracts import ingest_extraction, record_from_extraction
from .db import Database
from .documents import SUPPORTED_EXTS, load_pages, render_pdf_images
from .llm import LLM
from .prompts.contract import EXTRACT

MAX_BATCH_CHARS = 24_000
MAX_SCAN_IMAGES = 6
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

SCALAR_KEYS = (
    "contract_type", "effective_date", "term_start_date", "term_end_date", "auto_renewal",
    "exclusivity_type", "territory_text", "annual_sales_target", "annual_sales_target_currency",
    "confidence_score", "extraction_notes",
)
LIST_KEYS = (
    "minimum_order_quantities", "commercial_discount_rules", "product_price_list",
    "allowed_countries", "excluded_countries",
)

EXTRACT_SYSTEM = EXTRACT

_TIER = {
    "type": "object",
    "properties": {
        "from_container": {"type": ["integer", "null"]},
        "to_container": {"type": ["integer", "null"]},
        "discount_percent": {"type": ["number", "null"]},
        "notes": {"type": ["string", "null"]},
    },
    "required": ["from_container", "to_container", "discount_percent", "notes"],
}
_RULE = {
    "type": "object",
    "properties": {
        "title": {"type": ["string", "null"]},
        "section_reference": {"type": ["string", "null"]},
        "applies_per": {"type": ["string", "null"]},
        "basis": {"type": ["string", "null"]},
        "source_clause_text": {"type": ["string", "null"]},
        "tiers": {"type": "array", "items": _TIER},
    },
    "required": ["title", "section_reference", "applies_per", "basis", "source_clause_text", "tiers"],
}

EXTRACT_SCHEMA: dict[str, Any] = {
    "name": "contract_extraction",
    "strict": False,
    "schema": {
        "type": "object",
        "properties": {
            "contract_type": {"type": ["string", "null"]},
            "effective_date": {"type": ["string", "null"]},
            "term_start_date": {"type": ["string", "null"]},
            "term_end_date": {"type": ["string", "null"]},
            "auto_renewal": {"type": ["boolean", "null"]},
            "exclusivity_type": {"type": ["string", "null"]},
            "territory_text": {"type": ["string", "null"]},
            "allowed_countries": {"type": "array", "items": {"type": "string"}},
            "excluded_countries": {"type": "array", "items": {"type": "string"}},
            "annual_sales_target": {"type": ["object", "null"]},
            "annual_sales_target_currency": {"type": ["string", "null"]},
            "minimum_order_quantities": {"type": "array", "items": {"type": "object"}},
            "commercial_discount_rules": {"type": "array", "items": _RULE},
            "product_price_list": {"type": "array", "items": {"type": "object"}},
            "field_evidence": {"type": "object"},
            "confidence_score": {"type": ["number", "null"]},
            "extraction_notes": {"type": ["string", "null"]},
        },
        "required": ["commercial_discount_rules", "minimum_order_quantities", "allowed_countries", "excluded_countries", "field_evidence"],
    },
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _date(v: Any) -> str | None:
    s = str(v).strip() if v is not None else ""
    m = _DATE.search(s)
    return m.group(0) if m else None


def _countries(v: Any) -> list[str]:
    out: list[str] = []
    for c in v if isinstance(v, list) else []:
        code = re.sub(r"[^A-Za-z]", "", str(c)).upper()
        if len(code) == 2 and code not in out:
            out.append(code)
    return out


def _quote_kept(quote: str, page: int | None, pages: list[tuple[int, str]], image_pages: set[int]) -> bool:
    """A quote has to occur in the text we actually sent. A scanned page has no text to
    check against, so a quote cited on that page is kept."""
    q = _norm(quote)
    if len(q) < 8:
        return False
    if page in image_pages:
        return True
    hay = _norm("\n".join(t for _, t in pages if t))
    return q in hay


def sanitize_extraction(raw: dict[str, Any], pages: list[tuple[int, str]], image_pages: set[int]) -> dict[str, Any]:
    """Model output is not trusted: dates must be ISO, country codes must be two letters,
    and an evidence quote that is not in the document is dropped."""
    out: dict[str, Any] = {}
    for k in ("contract_type", "exclusivity_type", "territory_text", "annual_sales_target_currency", "extraction_notes"):
        v = raw.get(k)
        out[k] = v.strip() if isinstance(v, str) and v.strip() else None
    for k in ("effective_date", "term_start_date", "term_end_date"):
        out[k] = _date(raw.get(k))
    out["auto_renewal"] = raw.get("auto_renewal") if isinstance(raw.get("auto_renewal"), bool) else None
    target = raw.get("annual_sales_target")
    out["annual_sales_target"] = target if isinstance(target, dict) else None
    try:
        score = float(raw.get("confidence_score"))
        out["confidence_score"] = score if 0 <= score <= 1 else None
    except (TypeError, ValueError):
        out["confidence_score"] = None
    out["allowed_countries"] = _countries(raw.get("allowed_countries"))
    out["excluded_countries"] = _countries(raw.get("excluded_countries"))
    for k in ("minimum_order_quantities", "commercial_discount_rules", "product_price_list"):
        out[k] = [x for x in (raw.get(k) or []) if isinstance(x, dict)]
    evidence: dict[str, list[dict[str, Any]]] = {}
    fe = raw.get("field_evidence") if isinstance(raw.get("field_evidence"), dict) else {}
    for field, entries in fe.items():
        kept = []
        for e in entries or []:
            if not isinstance(e, dict):
                continue
            quote = str(e.get("quote") or "").strip()
            page = e.get("page")
            try:
                page_n = int(page) if page is not None else None
            except (TypeError, ValueError):
                page_n = None
            if quote and _quote_kept(quote, page_n, pages, image_pages):
                kept.append({"quote": quote, "page": page_n, "section_hint": e.get("section_hint")})
        if kept:
            evidence[str(field)] = kept
    out["field_evidence"] = evidence
    return out


def _has_terms(extraction: dict[str, Any]) -> bool:
    return any(extraction.get(k) for k in (
        "commercial_discount_rules", "minimum_order_quantities", "product_price_list",
        "allowed_countries", "excluded_countries", "term_start_date", "effective_date", "territory_text",
    ))


def _content(file_name: str, pages: list[tuple[int, str]], images: dict[int, bytes]) -> Any:
    parts_text = [f"Source file: {file_name}. Pages {pages[0][0]}–{pages[-1][0]}."]
    used = len(parts_text[0])
    for n, text in pages:
        block = f"--- Page {n} ---\n{(text or '(no text on this page — see the attached image)')[:8000]}"
        if used + len(block) > MAX_BATCH_CHARS:
            parts_text.append(f"--- Page {n} --- (omitted: batch too long)")
            continue
        parts_text.append(block)
        used += len(block)
    text = "\n\n".join(parts_text)
    if not images:
        return text
    import base64
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for n, _ in pages:
        jpeg = images.get(n)
        if not jpeg:
            continue
        url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
        content.append({"type": "text", "text": f"Image of page {n}."})
        content.append({"type": "image_url", "image_url": {"url": url}})
    return content


def _batches(pages: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    batches: list[list[tuple[int, str]]] = []
    cur: list[tuple[int, str]] = []
    size = 0
    for page in pages:
        n = len(page[1]) + 20
        if cur and size + n > MAX_BATCH_CHARS:
            batches.append(cur)
            cur, size = [], 0
        cur.append(page)
        size += n
    if cur:
        batches.append(cur)
    return batches or [[]]


def _merge(parts: list[dict[str, Any]]) -> dict[str, Any]:
    out = dict(parts[0])
    for p in parts[1:]:
        for k in LIST_KEYS:
            out[k] = [*(out.get(k) or []), *(p.get(k) or [])]
        fe = dict(out.get("field_evidence") or {})
        for k, v in (p.get("field_evidence") or {}).items():
            fe[k] = [*(fe.get(k) or []), *v]
        out["field_evidence"] = fe
        for k in SCALAR_KEYS:
            if out.get(k) in (None, "", []) and p.get(k) not in (None, "", []):
                out[k] = p[k]
    return out


def read_agreement(llm: LLM, file_name: str, data: bytes) -> dict[str, Any]:
    """The extraction dict, sanitized. Raises ValueError when the file cannot be read or
    contains no terms the code can enforce — nothing is written in that case."""
    if not file_name.lower().endswith(SUPPORTED_EXTS):
        raise ValueError(f"Unsupported agreement: {file_name} (use {', '.join(SUPPORTED_EXTS)})")
    pages = load_pages(file_name, data)
    if not pages:
        raise ValueError(f"No pages in {file_name!r}.")
    blank = [n for n, t in pages if not (t or "").strip()]
    images = render_pdf_images(data, blank[:MAX_SCAN_IMAGES]) if file_name.lower().endswith(".pdf") and blank else {}
    image_pages = set(images)
    parts: list[dict[str, Any]] = []
    for batch in _batches(pages):
        batch_images = {n: images[n] for n, _ in batch if n in images}
        raw = llm.complete_json(
            system=EXTRACT_SYSTEM,
            messages=[{"role": "user", "content": _content(file_name, batch, batch_images)}],
            schema=EXTRACT_SCHEMA, temperature=0, max_tokens=2500,
        )
        parts.append(sanitize_extraction(raw, pages, image_pages))
    extraction = _merge(parts) if len(parts) > 1 else parts[0]
    if not _has_terms(extraction):
        raise ValueError(f"No enforceable terms found in {file_name!r}.")
    return extraction


def read_and_ingest(
    db: Database, llm: LLM, *, brand_id: str, partnership_id: str, file_name: str, data: bytes, title: str | None = None,
) -> Any:
    """Read the file and write the contract, its terms and the derived PO rules."""
    extraction = read_agreement(llm, file_name, data)
    model = getattr(getattr(llm, "settings", None), "model", None)
    return ingest_extraction(
        db, extraction, brand_id=brand_id, partnership_id=partnership_id,
        title=title or file_name, file_name=file_name, file_sha256=hashlib.sha256(data).hexdigest(), model=model,
    )


def summarize(rec: Any) -> str:
    """What a person needs to hear after a filing. Numbers come from the stored record."""
    c, terms = rec.contract, rec.terms
    bits = [f"Filed {c.title or c.file_name} as the active agreement."]
    if c.term_start_date or c.term_end_date:
        bits.append(f"Term {c.term_start_date or '?'} to {c.term_end_date or '?'}.")
    if terms.discount_rules:
        bits.append(f"{len(terms.discount_rules)} discount schedule(s).")
    if terms.moqs:
        bits.append(f"{len(terms.moqs)} minimum-order clause(s).")
    allowed = [t.country_code for t in terms.territories if t.kind == "allowed"]
    excluded = [t.country_code for t in terms.territories if t.kind == "excluded"]
    if allowed:
        bits.append("Territories: " + ", ".join(allowed) + ".")
    if excluded:
        bits.append("Excluded: " + ", ".join(excluded) + ".")
    if rec.derived_rules:
        bits.append(f"{len(rec.derived_rules)} PO rule(s) now apply to orders.")
    if rec.skipped:
        bits.append("Left out, not clear enough to enforce: " + "; ".join(rec.skipped[:4]) + ".")
    bits.append("The previous active agreement for this partnership, if there was one, is superseded. The terms are on the Contracts page.")
    return " ".join(bits)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read an agreement file and store the terms it states.")
    ap.add_argument("file")
    ap.add_argument("--brand", required=True)
    ap.add_argument("--partnership", required=True)
    ap.add_argument("--title")
    ap.add_argument("--apply", action="store_true", help="write the contract and its derived PO rules")
    args = ap.parse_args(argv)
    from pathlib import Path

    from .llm import OpenAICompatLLM

    data = Path(args.file).read_bytes()
    llm = OpenAICompatLLM()
    if args.apply:
        rec = read_and_ingest(Database(), llm, brand_id=args.brand, partnership_id=args.partnership, file_name=Path(args.file).name, data=data, title=args.title)
    else:
        extraction = read_agreement(llm, Path(args.file).name, data)
        rec = record_from_extraction(extraction, brand_id=args.brand, partnership_id=args.partnership, title=args.title, file_name=Path(args.file).name)
    print(json.dumps({
        "contract_id": rec.contract.id, "written": args.apply, "summary": summarize(rec), "skipped": rec.skipped,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
