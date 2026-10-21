"""One-pass sales-report draft for the chat.

The model fills three slots: which database questions to ask, which current retail
news to look up, and which columns to chart. A bare request uses the standard
questions. text2sql runs the questions. web_search always runs at least once.
Chart points are read from the returned rows. Totals, shares, and changes are
computed from those rows before the prose is written. The prose follows the fixed
outline and is a draft for a person to check. It is not written into sales_reports.

A follow-up that only asks for charts does not take that path. The rows from the
draft already written are charted, and the outline is left where it is.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from ..db import Database, utcnow
from ..llm import LLM
from ..memory import attach, prompt_context, prompt_turns, standing_block, summary_block
from ..text2sql import QueryResult, answer_question, rerun
from ..tools.web_search import search
from .analyze import findings
from .charts import bind_charts, column_kinds, suggest_specs
from .prompt import CHART, SPLIT, STANDARD_QUESTIONS, WITH_DATA

MAX_QUESTIONS = 4
MAX_SEARCHES = 2
_QUESTION = 2_000
_SEARCH = 200
_SNIPPET = 300
_FALLBACK = "recent orders and sell-through for this partnership"
_BURST = timedelta(minutes=3)
_CHART_ASK = re.compile(
    r"(图表|画.{0,2}图|加图|作图|绘图|出图|可视化|\bcharts?\b|\bgraphs?\b|\bplots?\b)",
    re.IGNORECASE,
)
# A new draft, or a new figure. Either one is the full pipeline, not a chart on the last draft.
_FRESH = re.compile(
    r"(报告|草稿|重新|再查|查询|订单|销量|渠道|sku|期间|指标|report|draft|sell-through|purchase)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ReportDraft:
    text: str
    charts: list[dict[str, Any]]
    web_notes: list[str]
    warnings: list[str]
    data_query: dict[str, Any] | None
    series: list[dict[str, Any]] = field(default_factory=list)


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
                        "chart_type": {"type": "string", "enum": ["bar", "line", "pie"]},
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


def asks_only_for_charts(message: str) -> bool:
    """True when the message asks for a chart and does not ask for a new draft or new figures."""
    text = (message or "").strip()
    if not text or _FRESH.search(text):
        return False
    return _CHART_ASK.search(text) is not None


def has_report(history: list[dict[str, Any]] | None) -> bool:
    """A previous turn already wrote a draft, so a chart request has something to draw on."""
    return any(
        turn.get("role") == "assistant" and (turn.get("report_draft") or turn.get("report_series"))
        for turn in history or []
    )


def draft_report(
    db: Database, llm: LLM, *, message: str, history: list[dict[str, Any]],
    partnership_id: str, brand_id: str, distributor_id: str, session_id: str,
    hint: str | None = None, search_fn: Any = None, folded_through: int = 0, summary: str = "",
    charts_only: bool = False,
) -> ReportDraft:
    """Split, query, look up current events, chart, and write the standard outline."""
    draft = None
    for item in iter_draft(
        db, llm, message=message, history=history, partnership_id=partnership_id,
        brand_id=brand_id, distributor_id=distributor_id, session_id=session_id,
        hint=hint, search_fn=search_fn, folded_through=folded_through, summary=summary,
        charts_only=charts_only,
    ):
        if isinstance(item, ReportDraft):
            draft = item
    if draft is None:
        raise RuntimeError("report draft was not produced")
    return draft


def iter_draft(
    db: Database, llm: LLM, *, message: str, history: list[dict[str, Any]],
    partnership_id: str, brand_id: str, distributor_id: str, session_id: str,
    hint: str | None = None, search_fn: Any = None, folded_through: int = 0, summary: str = "",
    charts_only: bool = False,
) -> Iterator[dict[str, Any] | ReportDraft]:
    """Same work as `draft_report`, yielding a status before each slow step."""
    # Importing orchestrator.status at module level loads orchestrator.turn, which imports this module.
    from ..orchestrator.status import status

    if charts_only and has_report(history):
        yield from _chart_only(
            db, llm, message=message, history=history, partnership_id=partnership_id,
            brand_id=brand_id, distributor_id=distributor_id, session_id=session_id,
        )
        return

    remembered = prompt_context(standing_block(db, partnership_id), summary_block(summary))
    yield status("report-split", "拆问题", "正在决定要查什么、搜什么", state="running")
    questions, searches = _split(
        llm, message=message, history=history, hint=hint, remembered=remembered, folded_through=folded_through,
    )
    yield status("report-split", "拆问题", f"{len(questions)} 个问题，{len(searches)} 次检索", state="done")

    yield status("report-query", "查数", "正在查询订单和销量", state="running")
    asked = [
        _Ask(qid, question, answer_question(
            db, llm, question=question, partnership_id=partnership_id, brand_id=brand_id,
            distributor_id=distributor_id, session_id=session_id, reflect=False, history=history,
            folded_through=folded_through, summary=summary,
        ))
        for qid, question in questions
    ]
    yield status("report-query", "查数", f"查完 {len(asked)} 个问题", state="done")

    yield status("report-news", "新闻", "正在看近期零售动态", state="running")
    found = [_lookup(query, search_fn or search) for query in searches]
    yield status("report-news", "新闻", f"看了 {len(found)} 条检索", state="done")

    yield status("report-chart", "图表", "正在从查询结果里选图", state="running")
    charts = _charts(llm, message, asked)
    yield status("report-chart", "图表", f"{len(charts)} 张图" if charts else "这组数不画图", state="done")

    analyzed = _analysis(asked)
    yield status("report-write", "草稿", "正在按大纲写", state="running")
    text = _write(
        llm, message, history, asked, found, charts, analyzed,
        remembered=remembered, folded_through=folded_through,
    )
    yield status("report-write", "草稿", "草稿已写完", state="done")
    yield ReportDraft(
        text=text or "The report draft is empty.",
        charts=charts,
        web_notes=_notes(found),
        warnings=_warnings(asked, found),
        data_query=_mark(asked),
        series=series_payload(asked),
    )


def _chart_only(
    db: Database, llm: LLM, *, message: str, history: list[dict[str, Any]],
    partnership_id: str, brand_id: str, distributor_id: str, session_id: str,
) -> Iterator[dict[str, Any] | ReportDraft]:
    """Chart the last draft's rows. No new questions, no news, no rewritten outline."""
    from ..orchestrator.status import status

    yield status("report-chart", "图表", "正在用上一份查询结果画图", state="running")
    asked = saved_asks(history)
    if not asked:
        asked = _replay(db, session_id, partnership_id, brand_id, distributor_id)
    charts = _charts(llm, message, asked) if asked else []
    yield status("report-chart", "图表", f"{len(charts)} 张图" if charts else "这组数不画图", state="done")
    yield ReportDraft(
        text=_chart_note(message, charts),
        charts=charts,
        web_notes=[],
        warnings=[],
        data_query=None,
        series=series_payload(asked),
    )


def _split(
    llm: LLM, *, message: str, history: list[dict[str, Any]], hint: str | None,
    remembered: str = "", folded_through: int = 0,
) -> tuple[list[tuple[str, str]], list[str]]:
    recent = "\n".join(
        f"{turn['role']}: {str(turn.get('content') or '')[:500]}"
        for turn in prompt_turns(history, folded_through)
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
            system=attach(SPLIT, remembered), messages=[{"role": "user", "content": user}],
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
        {"id": item.id, "row_count": len(item.result.rows), "columns": column_kinds(item.result.rows)}
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
    charts = bind_charts(specs, series)
    if charts:
        return charts
    # The model may still return an empty list after the user asked for a chart.
    # A category or a time plus a number is enough to draw; a lone total is not.
    return bind_charts(suggest_specs(series, chinese=_cjk(message)), series)


def _analysis(asked: list[_Ask]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for item in asked:
        if item.result.ok and item.result.rows:
            blocks.extend(findings(item.id, item.result.rows))
    return blocks


def _write(
    llm: LLM, message: str, history: list[dict[str, Any]], asked: list[_Ask],
    found: list[_Search], charts: list[dict[str, Any]], analyzed: list[dict[str, Any]],
    *, remembered: str = "", folded_through: int = 0,
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
        "=== ANALYSIS (totals, shares, and changes computed from DATA; quote these, do not recompute) ===\n"
        f"{json.dumps(analyzed, ensure_ascii=False)}\n\n"
        "=== WEB (public context; not a source of sales figures) ===\n"
        f"{json.dumps(web, ensure_ascii=False)}\n\n"
        "=== CHARTS (drawn from DATA; mention by title only) ===\n"
        f"{json.dumps(titles, ensure_ascii=False)}\n"
    )
    messages = [
        {"role": turn.get("role"), "content": str(turn.get("content") or "")}
        for turn in prompt_turns(history, folded_through)
        if turn.get("role") in ("user", "assistant")
    ]
    messages.append({"role": "user", "content": prompt})
    return "".join(llm.stream_text(system=attach(WITH_DATA, remembered), messages=messages)).strip()


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


def series_payload(asked: list[_Ask]) -> list[dict[str, Any]]:
    """Rows a later chart-only turn can draw without asking the database again."""
    return [
        {"id": item.id, "question": item.question, "rows": item.result.rows}
        for item in asked
        if item.result.ok and item.result.rows
    ]


def saved_asks(history: list[dict[str, Any]] | None) -> list[_Ask]:
    """Rows stored on the latest draft. An older draft is not used once a newer one has none."""
    for turn in reversed(list(history or [])):
        if turn.get("role") != "assistant":
            continue
        raw = turn.get("report_series")
        if isinstance(raw, list) and raw:
            asked: list[_Ask] = []
            for item in raw:
                if not isinstance(item, dict):
                    continue
                rows = item.get("rows")
                if not isinstance(rows, list) or not rows:
                    continue
                asked.append(_Ask(
                    str(item.get("id") or f"q{len(asked) + 1}"),
                    str(item.get("question") or ""),
                    QueryResult(
                        ok=True, sql=None, rows=rows, row_count=len(rows),
                        error=None, error_type=None, tables=[],
                    ),
                ))
            if asked:
                return asked
        if turn.get("report_draft"):
            return []
    return []


def _replay(
    db: Database, session_id: str, partnership_id: str, brand_id: str, distributor_id: str,
) -> list[_Ask]:
    """Re-execute the last draft's statements when that draft did not keep its rows.

    Logs are newest first. One burst is the queries of one report, not an earlier chat turn.
    """
    logs = [
        row for row in db.list_text2sql_logs(partnership_id, limit=40)
        if row.get("session_id") == session_id and row.get("execution_success") and row.get("generated_sql")
    ]
    asked: list[_Ask] = []
    for index, row in enumerate(_latest_burst(logs), start=1):
        try:
            rows = rerun(
                db, str(row["generated_sql"]), partnership_id=partnership_id,
                brand_id=brand_id, distributor_id=distributor_id,
            )
        except Exception:
            continue
        if not rows:
            continue
        asked.append(_Ask(
            f"q{index}", str(row.get("question") or ""),
            QueryResult(
                ok=True, sql=str(row.get("generated_sql") or ""), rows=rows, row_count=len(rows),
                error=None, error_type=None, tables=list(row.get("selected_tables") or []),
                log_id=row.get("id") if isinstance(row.get("id"), int) else None,
            ),
        ))
    return asked


def _latest_burst(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not logs:
        return []
    newest = logs[0].get("created_at")
    if not isinstance(newest, datetime):
        return list(reversed(logs[:MAX_QUESTIONS]))
    burst: list[dict[str, Any]] = []
    for row in logs:
        created = row.get("created_at")
        if not isinstance(created, datetime) or newest - created > _BURST:
            break
        burst.append(row)
        if len(burst) >= MAX_QUESTIONS:
            break
    return list(reversed(burst))


def _chart_note(message: str, charts: list[dict[str, Any]]) -> str:
    chinese = _cjk(message)
    if charts:
        return "已按上一份草稿的查询结果补上图表。" if chinese else "Added charts from the last draft's figures."
    if chinese:
        return "上一份查询结果里没有可以做横轴的分类或时间，所以没有画图。"
    return "The last draft's figures have no category or time to chart."


def _cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text or "")
