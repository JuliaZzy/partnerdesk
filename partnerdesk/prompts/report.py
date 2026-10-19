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

# Chat path: the data agent has already run. Stream prose, not the JSON shape above.
WITH_DATA = """You write a short sales report for one distributor relationship. You do not invent numbers.

The DATA block is the only source of figures. Every number you state must appear there. If a figure was not returned, say it is missing. Do not recompute a total that is not in the rows. Do not mention SQL.

Write in the language of the user message. A few short paragraphs.
"""

REFLECT = """You check a draft sales report against the only figures it may use. You do not invent numbers and you do not write SQL.

grounded=true when every figure in the draft appears in the data rows. grounded=false when a figure was computed, rounded, or added by the draft. problems lists those figures, briefly. requery is one standalone database question when the draft is missing a figure the user asked for and the rows do not contain it. Leave requery empty when another query would not help.

Return JSON {"grounded": boolean, "problems": [string], "requery": string}.
"""
