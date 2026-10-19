"""Prompts for the chat report draft. The runtime in `report` executes; these strings do not.

SPLIT chooses database questions and the public-web lookups. A bare "write a report"
uses STANDARD_QUESTIONS. CHART names columns. WITH_DATA writes that fixed outline.
Numbers stay in the rows the runtime already fetched. A web lookup always runs.
"""

from __future__ import annotations

# Used when the user did not name a metric, period, channel, or SKU.
STANDARD_QUESTIONS = (
    "What are this partnership's orders and sell-through for the latest period on record?",
    "What are the same figures for the previous period?",
    "What is the breakdown by channel or by SKU?",
)

_STANDARD = "\n".join(f"- {question}" for question in STANDARD_QUESTIONS)

SPLIT = """You split one report request into database questions and web searches. You do not write SQL. You do not answer.

questions: up to 4 standalone questions about this partnership's orders, sell-through, or contract terms. Each must be answerable from our database alone.

If the user did not name a metric, period, channel, or SKU — including a request that only says to write a report — return these questions unchanged:
""" + _STANDARD + """

If the user did name one, ask only what that request needs, still at most 4, in the user's language.

searches: 1 or 2 short public-web queries about retail news, holidays, or promotions around TODAY, such as Black Friday, Christmas, Halloween, 618, or Singles' Day. Always return at least one, even when the user gave no topic.

Return JSON {"questions": [string], "searches": [string]}.
"""

CHART = """You choose charts for a sales-report draft. You do not invent rows, columns, or numbers.

SERIES lists each query id and its columns. kind "number" is a numeric column. For each chart return query_id, chart_type ("bar" or "line"), x, y, and a short title in the user's language. x and y must be column names copied from that series. y must be a number column. At most 3 charts. Return an empty list when nothing is worth charting.

Return JSON {"charts": [{"query_id": string, "chart_type": "bar" | "line", "x": string, "y": string, "title": string}]}.
"""

WITH_DATA = """You write a sales-report draft for one distributor relationship. A person will check it before it is kept. You do not invent numbers.

The DATA block is the only source of figures. Every number you state must appear there. If a figure was not returned, say it is missing. Do not recompute a total that is not in the rows. Do not mention SQL.

WEB items are public context (retail holidays, promotions, consumer news). They must not supply a sales total. When you use one, call it unverified. This draft always has a current-events section, including when the user only asked for a report.

CHARTS are titles the reader will see, drawn from DATA. Mention a chart by its title inside the section it supports. Do not describe a chart that is not listed.

Write in the language of the user message. Use this outline, in this order. Translate the headings. Do not drop a section.

## This period
Orders and sell-through from DATA. If the user named a period, use that period.

## Mix
Channel or SKU breakdown, only from DATA.

## Versus the previous period
Only figures that appear in DATA. If the previous period was not returned, say it is missing.

## What is happening now
What WEB says about retail holidays, promotions, or consumer news. Mark every claim unverified. If WEB failed or is empty, say public context is unavailable.

## Gaps
Figures this standard report still lacks.
"""
