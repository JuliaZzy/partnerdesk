"""Prompts for the chat report draft. The runtime in `report` executes; these strings do not.

SPLIT chooses database questions and the public-web lookups. A bare "write a report"
uses STANDARD_QUESTIONS. CHART names columns. WITH_DATA writes that fixed outline.
Numbers stay in the rows the runtime already fetched, or in the totals, shares, and
changes the runtime computed from those rows. A web lookup always runs.
"""

from __future__ import annotations

# Used when the user did not name a metric, period, channel, or SKU.
STANDARD_QUESTIONS = (
    "How many purchase orders does this partnership have, what is their total amount, "
    "and what is confirmed sell-through for the latest reporting period?",
    "How many purchase orders does this partnership have, what is their total amount, "
    "and what is confirmed sell-through for the previous reporting period?",
    "What is confirmed sell-through broken down by channel and by SKU?",
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

SERIES lists each query id, how many rows came back, and its columns. kind is "number", "time", or "text". For each chart return query_id, chart_type ("bar", "line", or "pie"), x, y, and a short title in the user's language. x and y must be column names copied from that series. y must be a number column. Use "line" when x is time. Use "pie" when x is text and the series is a share of at most 6 categories. Otherwise use "bar". At most 3 charts. When the user asked for a chart, chart every series that has a number column and a text or time column. Return an empty list only when no series has both. A single total with no category is not a chart.

Return JSON {"charts": [{"query_id": string, "chart_type": "bar" | "line" | "pie", "x": string, "y": string, "title": string}]}.
"""

WITH_DATA = """You write a sales-report draft for one distributor relationship. A person will check it before it is kept. You do not invent numbers.

DATA rows are the query results. ANALYSIS holds totals, shares, and changes already computed from those rows. Every number you state must appear in DATA or ANALYSIS. If a figure was not returned, say it is missing. Do not recompute a total, a share, or a change. Do not mention SQL.

WEB items are public context (retail holidays, promotions, consumer news). They must not supply a sales total. When you use one, call it unverified. This draft always has a current-events section, including when the user only asked for a report.

CHARTS are titles the reader will see, drawn from DATA. Mention a chart by its title inside the section it supports. Do not describe a chart that is not listed.

Write in the language of the user message. Use this outline, in this order. Translate the headings. Do not drop a section.

## This period
Orders and sell-through from DATA or ANALYSIS. If the user named a period, use that period.

## Mix
Channel or SKU breakdown, only from DATA or ANALYSIS.

## Versus the previous period
Only figures that appear in DATA or ANALYSIS. If the previous period was not returned, say it is missing.

## What is happening now
What WEB says about retail holidays, promotions, or consumer news. Mark every claim unverified. If WEB failed or is empty, say public context is unavailable.

## Gaps
Figures this standard report still lacks.
"""
