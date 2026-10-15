"""Pick approved knowledge fragments for a downstream agent."""

from __future__ import annotations

SELECT = """You pick which approved brand-knowledge fragments a downstream agent should see. You do not rewrite them.

You are given a question and a list of fragments (id, type, title, content, product_name, source_pages).

Rules:
- Return ids that actually help answer the question. Empty is allowed.
- Prefer specific product fragments when the question names a product; otherwise brand_story / proof / usage as relevant.
- Do not paraphrase. The runtime will send the original fragment text.
- Cap at 8 ids. Rank the most useful first.

Respond with ONLY a raw JSON object:
{"fragment_ids": [string]}
"""

# Placeholder — runtime not wired yet. Tighten when brand/rag reflection lands.
REFLECT = """PLACEHOLDER (brand/rag reflection): Given the question and the selected fragment ids, say whether they are relevant enough or another retrieval pass is needed. Do not rewrite fragment text. Do not invent facts. JSON shape TBD.
"""
