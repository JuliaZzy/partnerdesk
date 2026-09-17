"""Two-pass distillation of uploaded brand material into typed knowledge fragments.

Pass 1 asks an open question — "what kinds of content are in here?" — with no
output schema to fill in. Pass 2 then extracts fragments against whatever pass 1
found. The split exists because a single-pass extractor handed a fixed schema
quietly discards anything that doesn't fit it: hand it a product schema and the
brand-culture pages, the sales figures, and the audience profiles all vanish
without a trace. Asking first, extracting second, keeps them.

Long files are processed in page batches; each batch runs both passes and the
type registries are merged, so a 200-page catalogue never silently loses its tail.
"""

from __future__ import annotations

import os
import sys
import time

from .documents import LoadedDocument, SourcePage
from .gemini import generate_json, image_part, resolve_model, text_part
from .schemas import (
    SEED_TYPES,
    DiscoveredType,
    DistillResult,
    DocumentScan,
    FragmentBatch,
    KnowledgeFragment,
)

# Kept small so each batch is a single small model request: a 40-page batch built
# one ~30 MB request that proxies dropped outright, and retries could not save a
# request that never fits. ~12 pages of downscaled JPEG stays a few MB — inside
# what the transport tolerates — while still spanning enough pages for the extract
# pass to merge a product that runs across a spread into one fragment.
DEFAULT_PAGES_PER_BATCH = 12

_SEED_LIST = "\n".join(f"- {slug}: {desc}" for slug, desc in SEED_TYPES.items())

SCAN_SYSTEM = f"""You are cataloguing a brand's own sales and marketing material so it can later be used to write personalised B2B outreach emails.

Read every page you are given and report what kinds of content are actually present. Do not extract details yet — this pass only identifies what is here.

These are the content types we already know about:
{_SEED_LIST}

Rules:
- Only report a type if the material genuinely contains it. Do not pad the list.
- If a body of content does not fit any type above, invent a new snake_case slug for it and describe it. This matters more than reusing an existing type: a forced fit loses the content later.
- Record the 1-based page numbers where each type appears.
- Ignore pure decoration, agendas, title slides, and legal boilerplate."""

EXTRACT_SYSTEM = """You are distilling a brand's sales and marketing material into reusable knowledge fragments. These fragments will later be selected, a few at a time, to give an email-writing model accurate things to say about this brand and its products.

Write one fragment per distinct, self-contained piece of knowledge.

Rules:
- Each fragment must stand alone. A later reader sees only that fragment, never the source page, so never write "as shown above" or "this product" without naming it.
- Be concrete and keep real specifics: figures, percentages, certifications, ingredient or material names, market names, dates, award names. Vague marketing prose is worthless here — "high quality formulation" tells a reader nothing; "22% niacinamide, third-party tested for purity" does.
- Never invent, extrapolate, or round facts that are not in the material. If a claim is qualified or dated in the source, keep the qualifier.
- Use the type slugs identified in the scan. If a fragment genuinely fits none of them, use a new snake_case slug rather than forcing it into a close-enough one.
- Set product_name only for type 'product', copying the product name verbatim as written in the material.
- Record the 1-based source pages for every fragment so a human can verify it.
- Split a page covering several products into one fragment per product. Merge a single product spread across several pages into one fragment.
- Skip decoration, agendas, title slides, table-of-contents pages, and legal boilerplate."""


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _pages_per_batch() -> int:
    raw = (os.environ.get("BRAND_KNOWLEDGE_PAGES_PER_BATCH") or "").strip()
    try:
        val = int(raw)
    except ValueError:
        return DEFAULT_PAGES_PER_BATCH
    return max(1, min(val, 100))


def _page_parts(pages: list[SourcePage]) -> list:
    """Interleave page markers, text layer, and images in reading order."""
    parts: list = []
    for page in pages:
        parts.append(text_part(f"--- Page {page.page_number} ---"))
        if page.text:
            parts.append(text_part(page.text))
        for data, mime in page.images:
            parts.append(image_part(data, mime))
    return parts


def _scan(pages: list[SourcePage], filename: str) -> DocumentScan:
    parts = [
        text_part(f"Source file: {filename}. Pages {pages[0].page_number}–{pages[-1].page_number}."),
        *_page_parts(pages),
    ]
    return generate_json(parts, system=SCAN_SYSTEM, schema=DocumentScan, temperature=0.1)


def _extract(pages: list[SourcePage], filename: str, scan: DocumentScan) -> FragmentBatch:
    found = "\n".join(
        f"- {t.type} ({t.label}): {t.description} [pages {', '.join(str(p) for p in t.page_numbers) or 'unspecified'}]"
        for t in scan.types
    )
    parts = [
        text_part(
            f"Source file: {filename}. Pages {pages[0].page_number}–{pages[-1].page_number}.\n\n"
            f"Overview of this material:\n{scan.summary}\n\n"
            f"Content types identified in it:\n{found or '- (none identified)'}\n\n"
            "Now extract the knowledge fragments."
        ),
        *_page_parts(pages),
    ]
    return generate_json(parts, system=EXTRACT_SYSTEM, schema=FragmentBatch, temperature=0.2)


def _merge_types(into: dict[str, DiscoveredType], found: list[DiscoveredType]) -> None:
    for t in found:
        slug = t.type.strip().lower()
        if not slug:
            continue
        existing = into.get(slug)
        if existing is None:
            into[slug] = t.model_copy(update={"type": slug})
        else:
            merged = sorted(set(existing.page_numbers) | set(t.page_numbers))
            into[slug] = existing.model_copy(update={"page_numbers": merged})


def distill_document(doc: LoadedDocument) -> DistillResult:
    """Run both passes over every page batch and return the merged result."""
    if not doc.pages:
        raise ValueError(f"No readable pages in {doc.filename!r}.")

    batch_size = _pages_per_batch()
    batches = [doc.pages[i : i + batch_size] for i in range(0, len(doc.pages), batch_size)]
    t0 = time.perf_counter()
    _log(
        f"[brand-knowledge] {doc.filename!r}: {doc.pages_processed} page(s) "
        f"in {len(batches)} batch(es), model={resolve_model()!r}."
    )

    types: dict[str, DiscoveredType] = {}
    fragments: list[KnowledgeFragment] = []
    summaries: list[str] = []

    for idx, pages in enumerate(batches, start=1):
        span = f"{pages[0].page_number}-{pages[-1].page_number}"
        _log(f"[brand-knowledge] batch {idx}/{len(batches)} (pages {span}): scanning…")
        scan = _scan(pages, doc.filename)
        _merge_types(types, scan.types)
        if scan.summary.strip():
            summaries.append(scan.summary.strip())

        _log(
            f"[brand-knowledge] batch {idx}/{len(batches)}: found "
            f"{len(scan.types)} type(s), extracting…"
        )
        batch = _extract(pages, doc.filename, scan)
        for frag in batch.fragments:
            fragments.append(frag.model_copy(update={"type": frag.type.strip().lower()}))
        _log(f"[brand-knowledge] batch {idx}/{len(batches)}: {len(batch.fragments)} fragment(s).")

    _log(
        f"[brand-knowledge] {doc.filename!r} done: {len(fragments)} fragment(s), "
        f"{len(types)} type(s), {time.perf_counter() - t0:.1f}s."
    )

    return DistillResult(
        filename=doc.filename,
        page_count=doc.page_count,
        pages_processed=doc.pages_processed,
        summary=" ".join(summaries).strip(),
        types=list(types.values()),
        fragments=fragments,
        model=resolve_model(),
    )
