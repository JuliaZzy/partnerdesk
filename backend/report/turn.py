"""对话里的销售报告，一次写成草稿。

规划按固定六节给出数据库问题和网页检索。text2sql 执行取数。
业绩和库存两节画图。下一周期计划和问题与风险才检索公开网页。
查回的行交给一段 Python 算出洞察，再交给撰写。先写其余五节，再写摘要。
整份写完后打分。85 分及以上过关。低于 85 分把意见交回撰写修改，最多 3 轮，每轮改完再打分。
草稿不会写入 sales_reports。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..db import Database, utcnow
from ..llm import LLM
from ..memory import attach, prompt_context, prompt_turns, standing_block, summary_block
from ..text2sql import QueryResult, answer_question, run_statement
from ..text2sql.table_metadata import catalog_text
from ..tools.web_search import search
from .analyze import column_kinds
from .prompt import (
    CHART_GENERATION_PROMPT,
    CODE_WIZARD_PROMPT,
    REPORT_QUESTIONS_PROMPT,
    REPORT_REVIEW_PROMPT,
    REPORT_REVISE_PROMPT,
    SECTIONS,
    SUMMARY_PROMPT,
    WRITING_PROMPT,
    outline_text,
)
from .reflect import format_failures, with_reflection
from .wizard import execute

MAX_SECTION_QUESTIONS = 3
MAX_SECTION_SEARCHES = 2
PASS_SCORE = 85
MAX_REVIEW_ROUNDS = 3
_CHAPTER_POINTS = 20
_REQUIRED_CHAPTERS = ("关键指标", "摘要", "业绩表现", "库存情况", "下一周期计划")
_QUESTION = 2_000
_SEARCH = 200
_SNIPPET = 300
_FALLBACK = "这个合作关系最近的订单和动销"


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


def plan_schema() -> dict[str, Any]:
    return {
        "name": "report_plan", "strict": True,
        "schema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "sections": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "id": {"type": "string", "enum": [section["id"] for section in SECTIONS]},
                        "questions": {"type": "array", "items": {"type": "string"}},
                        "searches": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["id", "questions", "searches"],
                }},
            },
            "required": ["sections"],
        },
    }


def code_wizard_schema() -> dict[str, Any]:
    return {
        "name": "code_wizard", "strict": True,
        "schema": {
            "type": "object", "additionalProperties": False,
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
    }


def section_schema() -> dict[str, Any]:
    return {
        "name": "report_section", "strict": False,
        "schema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "content": {"type": "string"},
                "key_points": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["content", "key_points"],
        },
    }


def chart_schema() -> dict[str, Any]:
    """The chart object the prompt asks for. `echarts_option` stays open: a closed schema would drop axes and series."""
    return {
        "name": "report_charts", "strict": False,
        "schema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "charts": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                        "subtitle": {"type": "string"},
                        "type": {
                            "type": "string",
                            "enum": ["line", "bar", "pie", "horizontal_bar", "scatter", "radar"],
                        },
                        "echarts_option": {"type": "object"},
                    },
                    "required": ["id", "title", "subtitle", "type", "echarts_option"],
                }},
            },
            "required": ["charts"],
        },
    }


@dataclass(frozen=True)
class SectionPlan:
    id: str
    title: str
    brief: str
    sql: bool
    chart: bool
    search: bool
    require_search: bool
    questions: list[str]
    searches: list[str]


def default_searches(today: date) -> dict[str, list[str]]:
    """Public lookups used when a section that must search comes back empty."""
    return {
        "next": [
            f"{today.year}年{today.month}月 零售档期 促销热点",
            "即将到来的节假日 零售促销",
        ],
    }


def _clean(items: Any, cap: int, limit: int) -> list[str]:
    kept: list[str] = []
    seen: set[str] = set()
    if not isinstance(items, list):
        return kept
    for item in items:
        if len(kept) >= cap or not isinstance(item, str):
            continue
        text = " ".join(item.split())[:limit]
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        kept.append(text)
    return kept


def parse_plan(raw: Any, *, today: date | None = None) -> list[SectionPlan]:
    """Keep the six sections. Questions are only the ones the model wrote."""
    today = today or utcnow().date()
    supplied: dict[str, dict[str, Any]] = {}
    if isinstance(raw, dict):
        for item in raw.get("sections") or []:
            if not isinstance(item, dict):
                continue
            sid = str(item.get("id") or "")
            if sid and sid not in supplied:
                supplied[sid] = item
    defaults = default_searches(today)
    planned: list[SectionPlan] = []
    for section in SECTIONS:
        item = supplied.get(str(section["id"]))
        questions: list[str] = []
        searches: list[str] = []
        if section["sql"]:
            questions = _clean((item or {}).get("questions"), MAX_SECTION_QUESTIONS, _QUESTION) if item else []
        if section["search"]:
            if item is not None and "searches" in item:
                searches = _clean(item.get("searches"), MAX_SECTION_SEARCHES, _SEARCH)
            if section["require_search"] and not searches:
                searches = list(defaults.get(str(section["id"]), []))
        planned.append(SectionPlan(
            id=str(section["id"]),
            title=str(section["title"]),
            brief=str(section["brief"]),
            sql=bool(section["sql"]),
            chart=bool(section["chart"]),
            search=bool(section["search"]),
            require_search=bool(section["require_search"]),
            questions=questions,
            searches=searches,
        ))
    return planned


def iter_draft(
    db: Database, llm: LLM, *, message: str, history: list[dict[str, Any]],
    partnership_id: str, brand_id: str, distributor_id: str, session_id: str,
    hint: str | None = None, search_fn: Any = None, folded_through: int = 0, summary: str = "",
) -> Iterator[dict[str, Any] | ReportDraft]:
    """Yield a status around each slow step, then the draft."""
    # Importing orchestrator.status at module level loads orchestrator.turn, which imports this module.
    from ..orchestrator.status import status

    remembered = prompt_context(standing_block(db, partnership_id), summary_block(summary))
    yield status("report-plan", "规划", "正在按大纲安排每一节要查什么", state="running")
    planned = _plan(
        llm, message=message, history=history, hint=hint, remembered=remembered, folded_through=folded_through,
    )
    question_count = len({question.casefold() for section in planned for question in section.questions})
    search_count = len({query.casefold() for section in planned for query in section.searches})
    yield status("report-plan", "规划", f"{question_count} 个问题，{search_count} 次检索", state="done")

    yield status("report-query", "查数", "正在查询目标、业绩和库存", state="running")
    asked, by_section, failures = _query(
        db, llm, planned, partnership_id=partnership_id, brand_id=brand_id, distributor_id=distributor_id,
        session_id=session_id, history=history, folded_through=folded_through, summary=summary,
    )
    yield status("report-query", "查数", f"查完 {len(asked)} 个问题", state="done")

    labels = _attach_product_labels(db, brand_id, asked)
    yield status("report-chart", "图表", "正在给业绩和库存配图", state="running")
    charts: list[dict[str, Any]] = []
    charts_for: dict[str, list[dict[str, Any]]] = {}
    used_chart_ids: set[str] = set()
    chart_failures: list[str] = []
    for section in planned:
        if not section.chart:
            continue
        made, fails = _charts(llm, message, by_section[section.id])
        made = _assign_chart_ids(made, used_chart_ids)
        made = relabel_chart_labels(made, labels)
        charts_for[section.id] = made
        charts.extend(made)
        chart_failures.extend(fails)
    yield status("report-chart", "图表", f"{len(charts)} 张图" if charts else "这组数不画图", state="done")

    yield status("report-news", "新闻", "正在看热点和节假日", state="running")
    found = [_lookup(query, search_fn or search) for query in _search_queries(planned)]
    yield status("report-news", "新闻", f"看了 {len(found)} 条检索", state="done")

    yield status("report-insight", "洞察", "正在按章节计算", state="running")
    query_results = _query_results(asked)
    insights_by_section: dict[str, list[dict[str, Any]]] = {}
    insight_failures: list[str] = []
    insight_errors: list[str] = []
    for section in planned:
        if section.id == "summary":
            continue
        got, err, fails = _code_wizard(llm, section, query_results)
        insights_by_section[section.id] = got
        if err:
            insight_errors.append(f"{section.title}: {err}")
        insight_failures.extend(fails)
    insight_count = sum(len(items) for items in insights_by_section.values())
    yield status("report-insight", "洞察", f"{insight_count} 条" if insight_count else "没有算出洞察", state="done")

    texts: dict[str, str] = {}
    yield status("report-write", "正文", "正在写除摘要以外的章节", state="running")
    for section in planned:
        if section.id == "summary":
            continue
        owned = charts_for.get(section.id, []) if section.chart else []
        content, _points = _write_section(
            llm, message, history, section, _asks_for(section, by_section), found, owned,
            _insights_for(section, insights_by_section),
            remembered=remembered, folded_through=folded_through,
        )
        if section.id == "risks" and not content.strip():
            continue
        content = finish_section(content, labels, owned if section.chart else [])
        texts[section.id] = content or "这一节没有写成。"
    written = sum(1 for section in planned if section.id != "summary" and texts.get(section.id, "").strip())
    yield status("report-write", "正文", f"{written} 节已写完", state="done")

    yield status("report-summary", "摘要", "正在根据已写章节写摘要", state="running")
    summary_text, _points = _write_summary(
        llm, message, history, _body_for_summary(planned, texts),
        remembered=remembered, folded_through=folded_through,
    )
    texts["summary"] = finish_section(strip_chart_refs(summary_text), labels, []) or "摘要没有写成。"
    yield status("report-summary", "摘要", "摘要已写完", state="done")

    report = _assemble(planned, texts) or "The report draft is empty."
    evidence = _report_evidence(asked, insights_by_section, found, charts)
    yield status("report-review", "审核", "正在给整份报告打分", state="running")
    report, score, review_error = _review_rounds(
        llm, message=message, report=report, evidence=evidence, remembered=remembered,
    )
    if review_error or score is None:
        review_note = "审核没有完成"
    elif score >= PASS_SCORE:
        review_note = f"{score} 分，过关"
    else:
        review_note = f"{score} 分，未过关"
    yield status("report-review", "审核", review_note, state="done")
    report = finish_report(report, labels, planned, charts_for)

    warnings = _warnings(asked, found)
    warnings.extend(failures)
    warnings.extend(chart_failures)
    warnings.extend(insight_failures)
    if insight_errors and not insight_failures:
        warnings.append("Insights were not computed: " + "; ".join(insight_errors)[:160])
    if review_error:
        warnings.append(f"报告审核没有完成: {review_error[:160]}")
    needs_chart = any(
        item.result.ok and item.result.rows
        for section in planned if section.chart
        for item in by_section[section.id]
    )
    if needs_chart and not charts:
        warnings.append("业绩表现和库存情况没有画成图")
    yield ReportDraft(
        text=report,
        charts=charts,
        web_notes=_notes(found),
        warnings=warnings,
        data_query=_mark(asked),
        series=series_payload(asked),
    )


def _plan(
    llm: LLM, *, message: str, history: list[dict[str, Any]], hint: str | None,
    remembered: str = "", folded_through: int = 0,
) -> list[SectionPlan]:
    recent = "\n".join(
        f"{turn['role']}: {str(turn.get('content') or '')[:500]}"
        for turn in prompt_turns(history, folded_through)
        if turn.get("role") in ("user", "assistant") and str(turn.get("content") or "").strip()
    )
    user = (
        f"=== 今天 ===\n{utcnow().date().isoformat()}\n\n"
        f"=== 最近对话 ===\n{recent or '（无）'}\n\n"
        f"=== 规划问题 ===\n{(hint or '').strip() or '（无）'}"
    )
    try:
        raw = llm.complete_json(
            system=attach(REPORT_QUESTIONS_PROMPT.format(
                max_questions=MAX_SECTION_QUESTIONS,
                max_searches=MAX_SECTION_SEARCHES,
                message=(message or "").strip()[:4000] or "（无文字）",
                database_structure=catalog_text(),
                outline=outline_text(),
            ), remembered),
            messages=[{"role": "user", "content": user}],
            schema=plan_schema(), temperature=0, max_tokens=1500,
        )
    except Exception:
        raw = {}
    return parse_plan(raw, today=utcnow().date())


def _query(
    db: Database, llm: LLM, planned: list[SectionPlan], *,
    partnership_id: str, brand_id: str, distributor_id: str, session_id: str,
    history: list[dict[str, Any]], folded_through: int, summary: str,
) -> tuple[list[_Ask], dict[str, list[_Ask]], list[str]]:
    """Run each distinct question once. A failed statement is revised at most twice more."""
    seen: dict[str, _Ask] = {}
    asked: list[_Ask] = []
    failures: list[str] = []
    by_section: dict[str, list[_Ask]] = {section.id: [] for section in planned}
    for section in planned:
        for question in section.questions:
            key = question.casefold()
            item = seen.get(key)
            if item is None:
                item, note = _ask(
                    db, llm, question=question, qid=f"q{len(asked) + 1}",
                    partnership_id=partnership_id, brand_id=brand_id, distributor_id=distributor_id,
                    session_id=session_id, history=history, folded_through=folded_through, summary=summary,
                )
                seen[key] = item
                asked.append(item)
                if note:
                    failures.append(note)
            by_section[section.id].append(item)
    return asked, by_section, failures


def _ask(
    db: Database, llm: LLM, *, question: str, qid: str,
    partnership_id: str, brand_id: str, distributor_id: str, session_id: str,
    history: list[dict[str, Any]], folded_through: int, summary: str,
) -> tuple[_Ask, str]:
    first = answer_question(
        db, llm, question=question, partnership_id=partnership_id, brand_id=brand_id,
        distributor_id=distributor_id, session_id=session_id, reflect=False, history=history,
        folded_through=folded_through, summary=summary,
    )

    def run(sql: str) -> tuple[bool, QueryResult, str | None, str]:
        if not seen["done"]:
            seen["done"] = True
            return first.ok, first, first.error, ""
        result = run_statement(
            db, sql, question=question, partnership_id=partnership_id, brand_id=brand_id,
            distributor_id=distributor_id, session_id=session_id, tables=first.tables,
        )
        return result.ok, result, result.error, ""

    seen = {"done": False}
    value, attempts = with_reflection(llm, kind="sql", first=first.sql or "", run=run)
    chosen = value if isinstance(value, QueryResult) else first
    return _Ask(qid, question, chosen), format_failures(attempts)


def _asks_for(section: SectionPlan, by_section: dict[str, list[_Ask]]) -> list[_Ask]:
    """Next period and risks read the performance and inventory rows. They do not query again."""
    asked = list(by_section[section.id])
    if section.id not in {"next", "risks"}:
        return asked
    seen = {item.id for item in asked}
    for source in ("performance", "inventory"):
        for item in by_section.get(source, []):
            if item.id in seen:
                continue
            seen.add(item.id)
            asked.append(item)
    return asked


def _search_queries(planned: list[SectionPlan]) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()
    for section in planned:
        for query in section.searches:
            key = query.casefold()
            if key in seen:
                continue
            seen.add(key)
            queries.append(query)
    return queries


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


def _echarts_charts(specs: Any) -> list[dict[str, Any]]:
    """Keep charts that already carry an ECharts option."""
    if not isinstance(specs, list):
        return []
    kept: list[dict[str, Any]] = []
    for spec in specs:
        if len(kept) >= 2 or not isinstance(spec, dict):
            continue
        option = spec.get("echarts_option")
        title = str(spec.get("title") or "").strip()
        if not title or not isinstance(option, dict) or not option:
            continue
        kept.append({
            "id": str(spec.get("id") or f"chart_{len(kept) + 1:03d}"),
            "title": title[:120],
            "subtitle": str(spec.get("subtitle") or "").strip()[:120],
            "type": str(spec.get("type") or ""),
            "echarts_option": option,
        })
    return kept


def _charts(llm: LLM, message: str, asked: list[_Ask]) -> tuple[list[dict[str, Any]], list[str]]:
    query_results = []
    for item in asked:
        if not item.result.ok or not item.result.rows:
            continue
        columns = column_kinds(item.result.rows)
        if not columns:
            continue
        query_results.append({
            "id": item.id,
            "question": item.question,
            "row_count": len(item.result.rows),
            "columns": columns,
            "rows": item.result.rows,
        })
    if not query_results:
        return [], []
    try:
        raw = llm.complete_json(
            system=CHART_GENERATION_PROMPT.format(
                message=(message or "").strip()[:4000] or "（无文字）",
                query_results=json.dumps(query_results, ensure_ascii=False),
            ),
            messages=[{"role": "user", "content": "按查询结果选择图表。"}],
            schema=chart_schema(), temperature=0, max_tokens=4_000,
        )
    except Exception as err:
        raw = {"error": str(err).split("\n", 1)[0][:300]}

    def run(text: str) -> tuple[bool, list[dict[str, Any]], str | None, str]:
        try:
            parsed = json.loads(text) if text.strip().startswith("{") or text.strip().startswith("[") else {}
        except json.JSONDecodeError as err:
            return False, [], f"图表 JSON 无法解析: {err.msg}", ""
        specs = parsed.get("charts") if isinstance(parsed, dict) else parsed
        charts = _echarts_charts(specs)
        if charts:
            return True, charts, None, ""
        return False, [], "图表 JSON 没有可用的 echarts_option", ""

    charts, attempts = with_reflection(
        llm, kind="chart", first=json.dumps(raw, ensure_ascii=False), run=run,
    )
    return charts or [], [format_failures(attempts)] if attempts else []


def _query_results(asked: list[_Ask]) -> list[dict[str, Any]]:
    """Every successful text2sql result on this draft. Later chapters can read earlier rows."""
    results = []
    for item in asked:
        if not item.result.ok or not item.result.rows:
            continue
        results.append({
            "id": item.id,
            "question": item.question,
            "row_count": len(item.result.rows),
            "rows": item.result.rows[:50],
        })
    return results


def _insights_for(section: SectionPlan, by_section: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Next period and risks also cite the performance and inventory calculations."""
    sources = (section.id,) if section.id not in {"next", "risks"} else (section.id, "performance", "inventory")
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sid in sources:
        for item in by_section.get(sid, []):
            key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


def _code_wizard(
    llm: LLM, section: SectionPlan, query_results: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str | None, list[str]]:
    """Ask for analyze(datasets) for one chapter. datasets is every successful query."""
    if not query_results:
        return [], None, []
    try:
        raw = llm.complete_json(
            system=CODE_WIZARD_PROMPT.format(
                section_title=section.title,
                section_brief=section.brief,
                questions=json.dumps(section.questions, ensure_ascii=False),
                query_results=json.dumps(query_results, ensure_ascii=False, default=str),
            ),
            messages=[{"role": "user", "content": "只为当前章节写 analyze(datasets)。"}],
            schema=code_wizard_schema(), temperature=0, max_tokens=1_500,
        )
    except Exception as err:
        return [], str(err).split("\n", 1)[0][:200], []
    code = raw.get("code") if isinstance(raw, dict) else ""

    def run(snippet: str) -> tuple[bool, list[dict[str, Any]], str | None, str]:
        result = execute(str(snippet or ""), query_results)
        if result["error"]:
            return False, [], result["error"], ""
        return True, result["insights"], None, ""

    insights, attempts = with_reflection(llm, kind="code", first=str(code or ""), run=run)
    return insights or [], None, [format_failures(attempts)] if attempts else []


def _history(history: list[dict[str, Any]], folded_through: int) -> list[dict[str, Any]]:
    return [
        {"role": turn.get("role"), "content": str(turn.get("content") or "")}
        for turn in prompt_turns(history, folded_through)
        if turn.get("role") in ("user", "assistant")
    ]


def _section_payload(
    section: SectionPlan, asked: list[_Ask], found: list[_Search], charts: list[dict[str, Any]],
    insights: list[dict[str, Any]],
) -> str:
    wanted = {query.casefold() for query in section.searches}
    rows = [
        {
            "id": item.id, "question": item.question, "ok": item.result.ok,
            "rows": item.result.rows if item.result.ok else [], "error": item.result.error,
        }
        for item in asked
    ]
    payload = {"rows": rows, "insights": insights}
    web = [
        {"query": item.query, "ok": item.ok, "results": item.results if item.ok else [], "error": item.error}
        for item in found
        if item.query.casefold() in wanted
    ]
    drawn = [
        {
            "id": chart.get("id") or "",
            "title": chart.get("title") or "",
            "type": chart.get("type") or "",
        }
        for chart in charts
    ] if section.chart else []
    return (
        f"=== 查询结果 ===\n{json.dumps(payload, ensure_ascii=False)}\n\n"
        f"=== 新闻 ===\n{json.dumps(web, ensure_ascii=False) if web else '（本节不用）'}\n\n"
        f"=== 图表 ===\n{json.dumps(drawn, ensure_ascii=False) if drawn else '（本节不用）'}"
    )


def _read_section(raw: Any) -> tuple[str, list[str]]:
    if not isinstance(raw, dict):
        return "", []
    content = str(raw.get("content") or "").strip()
    points = [str(item).strip() for item in raw.get("key_points") or [] if str(item).strip()]
    return content, points


def _write_section(
    llm: LLM, message: str, history: list[dict[str, Any]], section: SectionPlan,
    asked: list[_Ask], found: list[_Search], charts: list[dict[str, Any]], insights: list[dict[str, Any]],
    *, remembered: str = "", folded_through: int = 0,
) -> tuple[str, list[str]]:
    messages = _history(history, folded_through)
    messages.append({"role": "user", "content": _section_payload(section, asked, found, charts, insights)})
    try:
        raw = llm.complete_json(
            system=attach(WRITING_PROMPT.format(
                message=(message or "").strip()[:4000] or _FALLBACK,
                section_title=section.title,
                section_brief=section.brief,
            ), remembered),
            messages=messages,
            schema=section_schema(), temperature=0.2, max_tokens=1200,
        )
    except Exception:
        return "", []
    return _read_section(raw)


def _write_summary(
    llm: LLM, message: str, history: list[dict[str, Any]], body: str,
    *, remembered: str = "", folded_through: int = 0,
) -> tuple[str, list[str]]:
    messages = _history(history, folded_through)
    messages.append({"role": "user", "content": f"=== 已写章节 ===\n{body}"})
    try:
        raw = llm.complete_json(
            system=attach(SUMMARY_PROMPT.format(
                message=(message or "").strip()[:4000] or _FALLBACK,
            ), remembered),
            messages=messages,
            schema=section_schema(), temperature=0.2, max_tokens=1200,
        )
    except Exception:
        return "", []
    return _read_section(raw)


def _body_for_summary(planned: list[SectionPlan], texts: dict[str, str]) -> str:
    parts: list[str] = []
    for index, section in enumerate(planned, start=1):
        if section.id == "summary":
            continue
        body = texts.get(section.id, "").strip()
        if not body:
            continue
        parts.append(f"## {index}. {section.title}\n\n{strip_chart_refs(body)}")
    return "\n\n".join(parts)


def _assemble(planned: list[SectionPlan], texts: dict[str, str]) -> str:
    parts: list[str] = []
    for index, section in enumerate(planned, start=1):
        body = texts.get(section.id, "").strip()
        if body:
            parts.append(f"## {index}. {section.title}\n\n{body}")
    return "\n\n".join(parts)


_SKU_KEYS = {"sku", "product_sku"}
_NAME_KEYS = {"product_name", "product_name_native", "product_name_en", "name"}
_CHART_REF = re.compile(r"\[([^\]\n]+)\]\((chart_[\w-]+)\)")
_SENTENCE = re.compile(r"[^。！？]+[。！？]?")
_TABLE_LINE = re.compile(r"^\s*\|")
_HEADING_LINE = re.compile(r"^#{1,6}\s+\S")
_BULLET_LINE = re.compile(r"^(?:[-*]|\d+[.)])\s+\S")
_LINK_LINE = re.compile(r"^\[[^\]\n]+\]\([^)\s]+\)\s*$")


def _row_sku(row: dict[str, Any]) -> str | None:
    for key, value in row.items():
        if str(key).strip().lower() in _SKU_KEYS and isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _row_name(row: dict[str, Any]) -> str:
    folded = {str(key).strip().lower(): value for key, value in row.items()}
    for key in _NAME_KEYS:
        value = folded.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def stamp_product_labels(rows: list[dict[str, Any]], names: dict[str, str]) -> dict[str, str]:
    """Attach product_label and return sku -> 「名称（SKU）」 for rows we can name."""
    labels: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        sku = _row_sku(row)
        if not sku:
            continue
        name = (names.get(sku) or "").strip() or _row_name(row)
        if name.endswith(f"（{sku}）"):
            label = name
        elif name and name != sku:
            label = f"{name}（{sku}）"
        else:
            continue
        row["product_label"] = label
        labels[sku] = label
    return labels


def _attach_product_labels(db: Database, brand_id: str, asked: list[_Ask]) -> dict[str, str]:
    rows: list[dict[str, Any]] = []
    skus: list[str] = []
    seen: set[str] = set()
    for item in asked:
        if not item.result.ok:
            continue
        for row in item.result.rows:
            if not isinstance(row, dict):
                continue
            rows.append(row)
            sku = _row_sku(row)
            if sku and sku not in seen:
                seen.add(sku)
                skus.append(sku)
    names = db.product_names(brand_id, skus) if skus else {}
    return stamp_product_labels(rows, names)


def rewrite_product_mentions(text: str, labels: dict[str, str]) -> str:
    """Replace a bare SKU with 「名称（SKU）」. A SKU already inside that form is left alone."""
    rewritten = text or ""
    for sku in sorted(labels, key=len, reverse=True):
        label = labels[sku]
        if not sku or not label or sku == label:
            continue
        pattern = re.compile(rf"(?<![A-Za-z0-9（-]){re.escape(sku)}(?![A-Za-z0-9）-])")
        rewritten = pattern.sub(label, rewritten)
    return rewritten


def strip_chart_refs(text: str) -> str:
    """Drop chart placeholders, keeping the title words. Web links stay."""
    return _CHART_REF.sub(r"\1", text or "")


def shape_prose(text: str) -> str:
    """Break a wall of sentences into short paragraphs. Tables, lists, headings, and lone links stay put."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    blocks: list[str] = []
    prose: list[str] = []

    def flush() -> None:
        if not prose:
            return
        flat = "".join(part.strip() for part in prose)
        prose.clear()
        sentences = [item.strip() for item in _SENTENCE.findall(flat) if item.strip()]
        if not sentences:
            if flat:
                blocks.append(flat)
            return
        if len(sentences) <= 2:
            blocks.append("".join(sentences))
            return
        for index in range(0, len(sentences), 2):
            blocks.append("".join(sentences[index:index + 2]))

    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            flush()
            index += 1
            continue
        if _TABLE_LINE.match(line):
            flush()
            table = [line.rstrip()]
            index += 1
            while index < len(lines) and lines[index].strip() and _TABLE_LINE.match(lines[index]):
                table.append(lines[index].rstrip())
                index += 1
            blocks.append("\n".join(table))
            continue
        if _HEADING_LINE.match(stripped) or _LINK_LINE.match(stripped):
            flush()
            blocks.append(stripped)
            index += 1
            continue
        if _BULLET_LINE.match(stripped):
            flush()
            items = [stripped]
            index += 1
            while index < len(lines):
                nxt = lines[index].strip()
                if not nxt:
                    look = index + 1
                    while look < len(lines) and not lines[look].strip():
                        look += 1
                    if look < len(lines) and _BULLET_LINE.match(lines[look].strip()):
                        index = look
                        continue
                    break
                if not _BULLET_LINE.match(nxt):
                    break
                items.append(nxt)
                index += 1
            blocks.append("\n".join(items))
            continue
        prose.append(stripped)
        index += 1
    flush()
    return "\n\n".join(blocks)


def ensure_chart_cites(text: str, charts: list[dict[str, Any]]) -> str:
    """Put a chart the section never mentioned at the end of that section."""
    missing = [
        f"[{str(chart.get('title') or '').strip()}]({chart.get('id')})"
        for chart in charts
        if chart.get("id") and str(chart.get("title") or "").strip() and str(chart.get("id")) not in (text or "")
    ]
    body = (text or "").rstrip()
    if not missing:
        return body
    extra = "\n\n".join(missing)
    return f"{body}\n\n{extra}" if body else extra


def finish_section(text: str, labels: dict[str, str], charts: list[dict[str, Any]]) -> str:
    """Names, paragraph breaks, then a citation for any chart this section still omitted."""
    shaped = shape_prose(rewrite_product_mentions(text, labels))
    return ensure_chart_cites(shaped, charts)


def _assign_chart_ids(charts: list[dict[str, Any]], used: set[str]) -> list[dict[str, Any]]:
    assigned: list[dict[str, Any]] = []
    for chart in charts:
        cid = str(chart.get("id") or "")
        if not cid or cid in used:
            number = 1
            while f"chart_{number:03d}" in used:
                number += 1
            cid = f"chart_{number:03d}"
        used.add(cid)
        assigned.append({**chart, "id": cid})
    return assigned


def _relabel_node(node: Any, labels: dict[str, str]) -> Any:
    if isinstance(node, str):
        return rewrite_product_mentions(node, labels)
    if isinstance(node, list):
        return [_relabel_node(item, labels) for item in node]
    if isinstance(node, dict):
        return {key: _relabel_node(value, labels) for key, value in node.items()}
    return node


def _fit_category_axes(option: dict[str, Any]) -> None:
    """Long product names need room. Short category labels are left as the model drew them."""

    def axes(key: str) -> list[dict[str, Any]]:
        raw = option.get(key)
        if isinstance(raw, dict):
            return [raw]
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        return []

    long_x = False
    for axis in axes("xAxis"):
        data = axis.get("data")
        if axis.get("type") == "value" or not isinstance(data, list):
            continue
        if any(isinstance(item, str) and len(item) > 8 for item in data):
            long_x = True
            label = axis.get("axisLabel") if isinstance(axis.get("axisLabel"), dict) else {}
            label.setdefault("interval", 0)
            label.setdefault("rotate", 28)
            label.setdefault("hideOverlap", True)
            axis["axisLabel"] = label
    for axis in axes("yAxis"):
        data = axis.get("data")
        if not isinstance(data, list):
            continue
        if any(isinstance(item, str) and len(item) > 8 for item in data):
            label = axis.get("axisLabel") if isinstance(axis.get("axisLabel"), dict) else {}
            label.setdefault("interval", 0)
            label.setdefault("width", 140)
            label.setdefault("overflow", "break")
            axis["axisLabel"] = label
    if not long_x:
        return
    grid = option.get("grid") if isinstance(option.get("grid"), dict) else {}
    grid["containLabel"] = True
    if grid.get("bottom") in (None, "3%", "5%", 0):
        grid["bottom"] = "22%"
    option["grid"] = grid


def relabel_chart_labels(charts: list[dict[str, Any]], labels: dict[str, str]) -> list[dict[str, Any]]:
    """Category names follow the catalog. The chart id stays a chart id."""
    relabeled: list[dict[str, Any]] = []
    for chart in charts:
        option = chart.get("echarts_option")
        copied = _relabel_node(option, labels) if isinstance(option, dict) else option
        if isinstance(copied, dict):
            _fit_category_axes(copied)
        relabeled.append({
            **chart,
            "title": rewrite_product_mentions(str(chart.get("title") or ""), labels),
            "subtitle": rewrite_product_mentions(str(chart.get("subtitle") or ""), labels),
            "echarts_option": copied,
        })
    return relabeled


def _section_heading_id(heading: str, planned: list[SectionPlan]) -> str | None:
    title = re.sub(r"^##\s+", "", heading).strip()
    title = re.sub(r"^\d+\.\s*", "", title)
    for section in planned:
        if title == section.title or title.endswith(section.title):
            return section.id
    return None


def place_charts_in_sections(
    report: str, planned: list[SectionPlan], charts_for: dict[str, list[dict[str, Any]]],
) -> str:
    """A chart stays in the section that owns it. A citation in another section becomes plain title text."""
    if not any(charts_for.values()):
        return report
    owned = {
        str(chart.get("id")): sid
        for sid, charts in charts_for.items()
        for chart in charts
        if chart.get("id")
    }

    def keep_owned(text: str, sid: str) -> str:
        def repl(match: re.Match[str]) -> str:
            if owned.get(match.group(2)) == sid:
                return match.group(0)
            return match.group(1)
        return _CHART_REF.sub(repl, text)

    rebuilt: list[str] = []
    for part in re.split(r"(?=^##\s+)", report or "", flags=re.M):
        if not part.strip():
            continue
        lines = part.split("\n", 1)
        heading = lines[0]
        body = lines[1] if len(lines) > 1 else ""
        if heading.startswith("##"):
            sid = _section_heading_id(heading, planned) or ""
            body = keep_owned(body, sid)
            body = ensure_chart_cites(body, charts_for.get(sid, []))
            rebuilt.append(heading + ("\n\n" + body.strip() if body.strip() else ""))
        else:
            rebuilt.append(keep_owned(part, "").strip())
    return "\n\n".join(part for part in rebuilt if part.strip())


def finish_report(
    report: str, labels: dict[str, str], planned: list[SectionPlan],
    charts_for: dict[str, list[dict[str, Any]]],
) -> str:
    """The revise step can undo names, breaks, and chart placement. Put them back on the final text."""
    named = rewrite_product_mentions(report, labels)
    placed = place_charts_in_sections(named, planned, charts_for)
    return shape_prose(placed) or named


def _report_evidence(
    asked: list[_Ask], insights_by_section: dict[str, list[dict[str, Any]]],
    found: list[_Search], charts: list[dict[str, Any]],
) -> str:
    """The facts the reviewer and the revising writer are allowed to use."""
    payload = {
        "query_results": _query_results(asked),
        "insights": [
            {"section": sid, **item}
            for sid, items in insights_by_section.items()
            for item in items
        ],
        "news": [
            {"query": item.query, "results": item.results[:4] if item.ok else []}
            for item in found
        ],
        "charts": [
            {"id": chart.get("id") or "", "title": chart.get("title") or ""}
            for chart in charts
        ],
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


def review_schema() -> dict[str, Any]:
    return {
        "name": "report_review", "strict": True,
        "schema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "chapters": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "section": {"type": "string", "enum": list(_REQUIRED_CHAPTERS)},
                        "score": {"type": "integer"},
                    },
                    "required": ["section", "score"],
                }},
                "risks_penalty": {"type": "integer"},
                "ok": {"type": "boolean"},
                "issues": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "section": {"type": "string"},
                        "issue_type": {
                            "type": "string",
                            "enum": ["hallucination", "logic", "exaggeration", "format", "incomplete"],
                        },
                        "severity": {"type": "string", "enum": ["critical", "major", "minor"]},
                        "description": {"type": "string"},
                        "suggestion": {"type": "string"},
                    },
                    "required": ["section", "issue_type", "severity", "description", "suggestion"],
                }},
            },
            "required": ["chapters", "risks_penalty", "ok", "issues"],
        },
    }


def revise_schema() -> dict[str, Any]:
    return {
        "name": "report_revise", "strict": True,
        "schema": {
            "type": "object", "additionalProperties": False,
            "properties": {"report": {"type": "string"}},
            "required": ["report"],
        },
    }


def _review_issues(raw: Any) -> list[dict[str, str]]:
    """Keep critical and major issues. Minor wording notes do not send the draft back."""
    if not isinstance(raw, dict):
        return []
    issues: list[dict[str, str]] = []
    for item in raw.get("issues") or []:
        if not isinstance(item, dict):
            continue
        section = str(item.get("section") or item.get("target_section") or "").strip()
        description = str(item.get("description") or item.get("problem") or "").strip()
        suggestion = str(item.get("suggestion") or "").strip()
        severity = str(item.get("severity") or "major").strip().lower()
        issue_type = str(item.get("issue_type") or "logic").strip().lower()
        if severity == "minor" or not section or not description:
            continue
        issues.append({
            "section": section,
            "issue_type": issue_type,
            "severity": severity,
            "description": description,
            "suggestion": suggestion,
        })
    return issues


def _chapter_points(value: Any) -> int:
    try:
        score = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(_CHAPTER_POINTS, score))


def _review_score(raw: Any) -> tuple[int | None, list[dict[str, int | str]]]:
    """Five required chapters, 20 points each. A bare score is only a fallback."""
    if not isinstance(raw, dict):
        return None, []
    found: dict[str, int] = {}
    for item in raw.get("chapters") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("section") or "").strip()
        if name in _REQUIRED_CHAPTERS:
            found[name] = _chapter_points(item.get("score"))
    if found:
        chapters = [{"section": name, "score": found.get(name, 0)} for name in _REQUIRED_CHAPTERS]
        try:
            penalty = int(raw.get("risks_penalty") or 0)
        except (TypeError, ValueError):
            penalty = 0
        penalty = max(0, min(_CHAPTER_POINTS, penalty))
        total = sum(int(item["score"]) for item in chapters) - penalty
        return max(0, min(100, total)), chapters
    if "score" not in raw:
        return None, []
    try:
        score = int(raw["score"])
    except (TypeError, ValueError):
        return None, []
    return max(0, min(100, score)), []


def _review_report(
    llm: LLM, *, message: str, report: str, evidence: str, previous_issues: str = "（无）",
    remembered: str = "",
) -> tuple[int | None, list[dict[str, str]], list[dict[str, int | str]], str | None]:
    """Score the current draft from five chapter marks. Later rounds check the last issues."""
    try:
        raw = llm.complete_json(
            system=attach(REPORT_REVIEW_PROMPT.format(
                message=(message or "").strip()[:4000] or _FALLBACK,
                outline=outline_text(),
                report=report[:12000],
                evidence=evidence[:12000],
                previous_issues=previous_issues or "（无）",
            ), remembered),
            messages=[{"role": "user", "content": "找出问题并给这整份报告打分。"}],
            schema=review_schema(), temperature=0, max_tokens=2_000,
        )
    except Exception as err:
        return None, [], [], str(err).split("\n", 1)[0][:200]
    score, chapters = _review_score(raw)
    if score is None:
        return None, [], [], "审核没有给出分数"
    return score, _review_issues(raw), chapters, None


def _review_rounds(
    llm: LLM, *, message: str, report: str, evidence: str, remembered: str = "",
) -> tuple[str, int | None, str | None]:
    """Score, revise when below 85, and score the revision. At most three revisions."""
    score: int | None = None
    error: str | None = None
    previous = "（无）"
    for round_index in range(1, MAX_REVIEW_ROUNDS + 1):
        score, issues, chapters, error = _review_report(
            llm, message=message, report=report, evidence=evidence,
            previous_issues=previous, remembered=remembered,
        )
        if score is not None and issues and score >= PASS_SCORE:
            score = PASS_SCORE - 1
        if error or score is None or score >= PASS_SCORE or not issues:
            return report, score, error
        revised = _revise_report(
            llm, message=message, report=report, score=score, chapters=chapters, issues=issues,
            evidence=evidence, remembered=remembered,
        )
        if not revised.strip():
            return report, score, error
        report = revised.strip()
        previous = json.dumps(issues, ensure_ascii=False)
        if round_index == MAX_REVIEW_ROUNDS:
            break
    score, issues, _, error = _review_report(
        llm, message=message, report=report, evidence=evidence,
        previous_issues=previous, remembered=remembered,
    )
    if score is not None and issues and score >= PASS_SCORE:
        score = PASS_SCORE - 1
    return report, score, error


def _revise_report(
    llm: LLM, *, message: str, report: str, score: int, chapters: list[dict[str, int | str]],
    issues: list[dict[str, str]], evidence: str, remembered: str = "",
) -> str:
    """One rewrite of the whole draft from the review. Empty means keep the original."""
    try:
        raw = llm.complete_json(
            system=attach(REPORT_REVISE_PROMPT.format(
                message=(message or "").strip()[:4000] or _FALLBACK,
                report=report[:12000],
                score=score,
                chapters=json.dumps(chapters, ensure_ascii=False),
                issues=json.dumps(issues, ensure_ascii=False),
                evidence=evidence[:12000],
            ), remembered),
            messages=[{"role": "user", "content": "按审核意见修改整份报告。"}],
            schema=revise_schema(), temperature=0.2, max_tokens=4000,
        )
    except Exception:
        return ""
    if not isinstance(raw, dict):
        return ""
    return str(raw.get("report") or "").strip()


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
    """Rows from this draft, kept on the message."""
    return [
        {"id": item.id, "question": item.question, "rows": item.result.rows}
        for item in asked
        if item.result.ok and item.result.rows
    ]

