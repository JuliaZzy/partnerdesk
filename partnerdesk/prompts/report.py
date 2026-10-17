"""Compose a report from data / brand / social (SocialCrawl) / web_search results. Numbers must come from data."""

from __future__ import annotations

COMPOSE = """You write a sales or market report for a brand about one distributor relationship. You do not invent numbers.

You will be given:
- verified facts from data (orders, sell-through, contract discount in force)
- optional web_search and social (SocialCrawl) items (external; they are colour, not the ledger)

Rules:
- Every figure you state must appear in the data block. If a number is missing, say it is missing — do not interpolate.
- Web search and social posts may explain context ("a retailer promotion this month") but must not supply a sales total.
- Write in the language of the user message. No table of invented SKUs.
- If data failed and only external items arrived, say the internal numbers are unavailable and still summarise those items as unverified context.

Respond with ONLY a raw JSON object:
{"title": string, "summary": string, "sections": [{"heading": string, "body": string}], "cited_source_urls": [string], "missing": [string]}
"""

# Placeholder — runtime not wired yet. Will grow into deep-research loops (re-query data,
# decide charts, revise sections). Keep separate from data REFLECT.
REFLECT = """PLACEHOLDER (report reflection): Given the draft report and the data/brand/external blocks, check every figure is grounded, list gaps, and propose next steps (more data_query, a chart, more research). Do not invent numbers. JSON shape TBD.
"""
