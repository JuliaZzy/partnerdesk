"""Brand-document distillation (scan then extract). RAG selection is rag.prompts."""

from __future__ import annotations

SCAN = """You are cataloguing a brand's own sales, product and marketing material so a distributor-facing assistant can later answer questions about the brand and its products accurately.

Read every page you are given and report what kinds of content are actually present. Do not extract details yet — this pass only identifies what is here.

These are the content types we already know about:
{{seed_list}}

Rules:
- Only report a type if the material genuinely contains it. Do not pad the list.
- If a body of content does not fit any type above, invent a new snake_case slug for it and describe it. This matters more than reusing an existing type: a forced fit loses the content later.
- Record the 1-based page numbers where each type appears.
- Ignore pure decoration, agendas, title slides, and legal boilerplate.

Respond with ONLY a raw JSON object (no markdown fence):
{"summary": string, "types": [{"type": string, "label": string, "description": string, "page_numbers": [number]}]}
"""

EXTRACT = """You are distilling a brand's sales, product and marketing material into reusable knowledge fragments. A few fragments at a time will later be handed to an assistant talking to the brand's distributors, so it has accurate, specific things to say about the brand and its products.

Write one fragment per distinct, self-contained piece of knowledge.

Rules:
- Each fragment must stand alone. A later reader sees only that fragment, never the source page, so never write "as shown above" or "this product" without naming it.
- Be concrete and keep real specifics: figures, percentages, certifications, ingredient or material names, market names, dates, award names. Vague marketing prose is worthless here — "high quality formulation" tells a reader nothing; "22% niacinamide, third-party tested for purity" does.
- Never invent, extrapolate, or round facts that are not in the material. If a claim is qualified or dated in the source, keep the qualifier.
- Use the type slugs identified in the scan. If a fragment genuinely fits none of them, use a new snake_case slug rather than forcing it into a close-enough one.
- Set product_name only for type 'product', copying the product name verbatim as written in the material.
- Record the 1-based source pages for every fragment so a human can verify it.
- Split a page covering several products into one fragment per product. Merge a single product spread across several pages into one fragment.
- Skip decoration, agendas, title slides, table-of-contents pages, and legal boilerplate.
- Write content in the language of the material.

Respond with ONLY a raw JSON object (no markdown fence):
{"fragments": [{"type": string, "title": string, "content": string, "product_name": string|null, "source_pages": [number], "tags": [string]}]}
"""
