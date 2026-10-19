"""LLM product resolver — the semantic fallback for the code-check's deterministic search.

Token-overlap search catches exact SKUs, shared words and sizes, but it can't bridge
meaning: an English "essence water" against a Chinese-only 精华水, or "the anti-aging
serum". When search returns ZERO matches for a reference, ask the model — ONCE per turn,
batched over all unresolved references — to map each to the SKU(s) it most plausibly
means. It never invents a SKU: the caller keeps only SKUs that exist in the catalog.

No synonym lists in the prompt. The model reasons over the full product attributes.
"""

from __future__ import annotations

import json

from ..llm import LLM


def build_resolver_prompt(references: list[str], products: list[dict[str, str]]) -> str:
    catalog = "\n".join(
        f"{p['sku']} | {' · '.join(x for x in (p['name'], p['native'], p['unit_size'], p['variant'], p['category']) if x)}"
        for p in products
    )
    refs = "\n".join(f"{i + 1}. {r}" for i, r in enumerate(references))
    shape = ", ".join(f'{json.dumps(r, ensure_ascii=False)}: ["SKU", ...]' for r in references)
    return f"""You match a distributor's informal product references to catalog SKUs. Reason over ALL attributes (English name, native name, size, variant, category) and across languages — an English phrase can mean a Chinese-only product and vice versa. Consider size ("100ml") and variant ("single"/"单支" vs "double"/"双支").

=== CATALOG (SKU | name · native · size · variant · category) ===
{catalog}

=== REFERENCES TO RESOLVE ===
{refs}

For EACH reference, return the SKU(s) it most plausibly means:
- exactly one clear match → one SKU
- a few plausible matches (e.g. size/variant not specified) → list them, best first
- nothing in the catalog fits → empty array
NEVER invent a SKU — only use SKUs from the catalog above. Do not force a match.

Respond with ONLY a raw JSON object mapping each reference string to an array of SKUs:
{{ {shape} }}"""


def resolve_references(llm: LLM, references: list[str], products: list[dict[str, str]]) -> dict[str, list[str]]:
    """reference (verbatim) → ordered candidate SKUs (may be empty). Raises on model
    failure — the caller leaves the reference unresolved."""
    if not references or not products:
        return {}
    raw = llm.complete_json(
        system=build_resolver_prompt(references, products),
        messages=[{"role": "user", "content": "Resolve the references above."}],
        temperature=0, max_tokens=400,
    )
    out: dict[str, list[str]] = {}
    for ref in references:
        v = raw.get(ref)
        out[ref] = [s.strip() for s in v if isinstance(s, str) and s.strip()] if isinstance(v, list) else []
    return out
