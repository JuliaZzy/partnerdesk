"""One-pass sales-report draft for the chat.

The model fills three slots: which database questions to ask, which current retail
news to look up, and which columns to chart. A bare request uses the standard
questions. text2sql runs the questions. web_search always runs at least once.
Chart points are read from the returned rows. The prose follows the fixed outline
and is a draft for a person to check. It is not written into sales_reports.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from ..db import Database, utcnow
from ..llm import LLM
from ..text2sql import QueryResult, answer_question
from ..tools.web_search import search
from .charts import bind_charts, column_kinds
from .prompt.report import CHART, SPLIT, STANDARD_QUESTIONS, WITH_DATA

MAX_QUESTIONS = 4
MAX_SEARCHES = 2
_QUESTION = 2_000
_SEARCH = 200
_SNIPPET = 300
_FALLBACK = "recent orders and sell-through for this partnership"


@dataclass(frozen=True)
class ReportDraft:
    text: str
    charts: list[dict[str, Any]]
    web_notes: list[str]
    warnings: list[str]
    data_query: dict[str, Any] | None


@dataclass
class _Ask:
    id: str
    question: str
    result: QueryResult


@dataclass
class _Search:
    query: str
    ok: bool
    results: list[dict[str, str]]
    error: str | None


def split_schema() -> dict[str, Any]:
    return {
        "name": "report_split", "strict": True,
        "schema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "questions": {"type": "array", "items": {"type": "string"}},
                "searches": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["questions", "searches"],
        },
    }


def chart_schema() -> dict[str, Any]:
    return {
        "name": "report_charts", "strict": True,
        "schema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "charts": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "query_id": {"type": "string"},
                        "chart_type": {"type": "string", "enum": ["bar", "line"]},
                        "x": {"type": "string"},
                        "y": {"type": "string"},
                        "title": {"type": "string"},
                    },
                    "required": ["query_id", "chart_type", "x", "y", "title"],
                }},
            },
            "required": ["charts"],
        },
    }


def default_search(today: date) -> str:
    """The lookup used when the model returns no web query. One search still runs."""
    return f"{today.year}年{today.month}月 零售档期 促销热点"


def parse_split(raw: Any, *, today: date | None = None) -> tuple[list[tuple[str, str]], list[str]]:
    """Cap the model's lists. No questions becomes the standard set. No search becomes one."""
    questions: list[str] = []
    seen: set[str] = set()
    if isinstance(raw, dict):
        for item in raw.get("questions") or []:
            if len(questions) >= MAX_QUESTIONS or not isinstance(item, str):
                continue
            text = " ".join(item.split())[:_QUESTION]
            key = text.casefold()
            if not text or key in seen:
                continue
            seen.add(key)
            questions.append(text)
    if not questions:
        questions = list(STANDARD_QUESTIONS)
    searches: list[str] = []
    seen_search: set[str] = set()
    if isinstance(raw, dict):
        for item in raw.get("searches") or []:
            if len(searches) >= MAX_SEARCHES or not isinstance(item, str):
                continue
            text = " ".join(item.split())[:_SEARCH]
            key = text.casefold()
            if not text or key in seen_search:
                continue
            seen_search.add(key)
            searches.append(text)
    if not searches:
        searches = [default_search(today or utcnow().date())]
    ids = [(f"q{index}", question) for index, question in enumerate(questions, start=1)]
    return ids, searches


def draft_report(
    db: Database, llm: LLM, *, message: str, history: list[dict[str, Any]],
    partnership_id: str, brand_id: str, distributor_id: str, session_id: str,
    hint: str | None = None, search_fn: Any = None,
) -> ReportDraft:
    """Split, query, look up current events, chart, and write the standard outline."""
    questions, searches = _split(llm, message=message, history=history, hint=hint)
    asked = [
        _Ask(qid, question, answer_question(
            db, llm, question=question, partnership_id=partnership_id, brand_id=brand_id,
            distributor_id=distributor_id, session_id=session_id, reflect=False,
        ))
        for qid, question in questions
    ]
    found = [_lookup(query, search_fn or search) for query in searches]
    charts = _charts(llm, message, asked)
    text = _write(llm, message, history, asked, found, charts)
    return ReportDraft(
        text=text or "The report draft is empty.",
        charts=charts,
        web_notes=_notes(found),
        warnings=_warnings(asked, found),
        data_query=_mark(asked),
    )


def _split(
    llm: LLM, *, message: str, history: list[dict[str, Any]], hint: str | None,
) -> tuple[list[tuple[str, str]], list[str]]:
    recent = "\n".join(
        f"{turn['role']}: {str(turn.get('content') or '')[:500]}"
        for turn in history[-8:]
        if turn.get("role") in ("user", "assistant") and str(turn.get("content") or "").strip()
    )
    user = (
        f"=== TODAY ===\n{utcnow().date().isoformat()}\n\n"
        f"=== RECENT CONVERSATION ===\n{recent or '(none)'}\n\n"
        f"=== PLANNER QUESTION ===\n{(hint or '').strip() or '(none)'}\n\n"
        f"=== MESSAGE ===\n{(message or '')[:4000] or '(no text)'}"
    )
    try:
        raw = llm.complete_json(
            system=SPLIT, messages=[{"role": "user", "content": user}],
            schema=split_schema(), temperature=0, max_tokens=400,
        )
    except Exception:
        raw = {}
    return parse_split(raw, today=utcnow().date())


def _lookup(query: str, search_fn: Any) -> _Search:
    try:
        rows = search_fn(query) or []
    except Exception as err:
        return _Search(query=query, ok=False, results=[], error=str(err).split("\n", 1)[0][:200])
    results: list[dict[str, str]] = []
    if isinstance(rows, list):
        for row in rows[:3]:
            if not isinstance(row, dict):
                continue
            results.append({
                "title": str(row.get("title") or "").strip()[:160],
                "snippet": str(row.get("snippet") or "").strip()[:_SNIPPET],
                "url": str(row.get("url") or "").strip()[:300],
            })
    return _Search(query=query, ok=True, results=results, error=None)


def _charts(llm: LLM, message: str, asked: list[_Ask]) -> list[dict[str, Any]]:
    series = {
        item.id: item.result.rows
        for item in asked
        if item.result.ok and item.result.rows
    }
    described = [
        {"id": item.id, "columns": column_kinds(item.result.rows)}
        for item in asked
        if item.id in series and column_kinds(item.result.rows)
    ]
    if not described:
        return []
    user = (
        f"=== MESSAGE ===\n{(message or '')[:4000] or '(no text)'}\n\n"
        f"=== SERIES ===\n{json.dumps(described, ensure_ascii=False)}"
    )
    try:
        raw = llm.complete_json(
            system=CHART, messages=[{"role": "user", "content": user}],
            schema=chart_schema(), temperature=0, max_tokens=500,
        )
    except Exception:
        raw = {}
    specs = raw.get("charts") if isinstance(raw, dict) else None
    return bind_charts(specs, series)


def _write(
    llm: LLM, message: str, history: list[dict[str, Any]], asked: list[_Ask],
    found: list[_Search], charts: list[dict[str, Any]],
) -> str:
    data = [
        {
            "id": item.id, "question": item.question, "ok": item.result.ok,
            "rows": item.result.rows if item.result.ok else [], "error": item.result.error,
        }
        for item in asked
    ]
    web = [
        {"query": item.query, "ok": item.ok, "results": item.results if item.ok else [], "error": item.error}
        for item in found
    ]
    titles = [{"title": chart["title"], "chart_type": chart["chart_type"]} for chart in charts]
    prompt = (
        f"{(message or '').strip() or _FALLBACK}\n\n"
        "=== DATA (queried from our database for this partnership; "
        "quote these figures, do not recompute or invent) ===\n"
        f"{json.dumps(data, ensure_ascii=False)}\n\n"
        "=== WEB (public context; not a source of sales figures) ===\n"
        f"{json.dumps(web, ensure_ascii=False)}\n\n"
        "=== CHARTS (drawn from DATA; mention by title only) ===\n"
        f"{json.dumps(titles, ensure_ascii=False)}\n"
    )
    messages = [
        {"role": turn.get("role"), "content": str(turn.get("content") or "")}
        for turn in history[-8:]
        if turn.get("role") in ("user", "assistant")
    ]
    messages.append({"role": "user", "content": prompt})
    return "".join(llm.stream_text(system=WITH_DATA, messages=messages)).strip()


def _notes(found: list[_Search]) -> list[str]:
    notes: list[str] = []
    for item in found:
        if not item.ok:
            continue
        for row in item.results:
            title = row.get("title") or ""
            if title and title not in notes:
                notes.append(title)
            if len(notes) >= 4:
                return notes
    return notes


def _warnings(asked: list[_Ask], found: list[_Search]) -> list[str]:
    warnings: list[str] = []
    for item in asked:
        if item.result.ok:
            continue
        detail = item.result.error or "query failed"
        warnings.append(f'No figures for "{item.question[:80]}": {detail[:160]}')
    for item in found:
        if not item.ok:
            warnings.append(f'Web search failed ("{item.query[:80]}"): {item.error or "search failed"}')
    return warnings


def _mark(asked: list[_Ask]) -> dict[str, Any] | None:
    chosen = next((item for item in asked if item.result.ok and item.result.log_id), None)
    if chosen is None:
        chosen = next((item for item in asked if item.result.log_id), None)
    if chosen is None or not chosen.result.log_id:
        return None
    result = chosen.result
    return {"ok": result.ok, "row_count": result.row_count, "error": result.error, "log_id": result.log_id}
