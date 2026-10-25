"""把一个自然语言问题变成这个合作关系表里的行。

模型选表，然后写一条 SELECT。代码解析这条语句，在已经排除其他合作关系的视图上执行，并返回行。
之后的聊天轮次可以引用这些行。它不重算它们。

失败后的改写走 `codefix`：执行，按报错再改，最多三次。仍失败就把最后一次语句和报错交给人工。
行回来之后打分，最多三次。80 分及以上就用这些行；低于 80 分可以换一个问题再查。
审过的改正不会自己喂回提示词——一次改错会让下一个问题学会同一个错误。
失败和改对的尝试记在 `text2sql_logs` 上，供 `analyze_badcase` 看。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from ..codefix import format_failures, with_reflection
from ..db import Database, utcnow
from ..llm import LLM
from ..memory import attach, prompt_context, prompt_turns, standing_block, summary_block
from .prompt import PICK, REFLECT, SQL
from .sql_validator import validate
from .table_metadata import TABLES, catalog_text, describe_tables, expand_tables, scope_views

_MAX_QUESTION = 2_000
_MAX_CELL = 400
_MAX_ROWS = 50
# The reflect prompt states this same cutoff.
REFLECT_PASS = 80
MAX_REFLECTIONS = 3
_FENCE = re.compile(r"^```(?:sql)?\s*([\s\S]*?)\s*```$", re.IGNORECASE)


@dataclass
class QueryResult:
    ok: bool
    sql: str | None
    rows: list[dict[str, Any]]
    row_count: int
    error: str | None
    error_type: str | None
    tables: list[str]
    log_id: int | None = None
    handoff: str = ""
    score: int | None = None


def pick_schema() -> dict[str, Any]:
    return {
        "name": "select_tables", "strict": True,
        "schema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "tables": {"type": "array", "items": {"type": "string", "enum": list(TABLES)}},
            },
            "required": ["tables"],
        },
    }


def sql_schema() -> dict[str, Any]:
    return {
        "name": "select_sql", "strict": True,
        "schema": {
            "type": "object", "additionalProperties": False,
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        },
    }


def _strip_fence(sql: str) -> str:
    text = (sql or "").strip()
    match = _FENCE.match(text)
    return (match.group(1) if match else text).strip()


def _jsonable(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return None
    if isinstance(value, str) and len(value) > _MAX_CELL:
        return value[:_MAX_CELL] + "…"
    if isinstance(value, dict):
        return {str(key): _jsonable(item, depth + 1) for key, item in list(value.items())[:30]}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item, depth + 1) for item in list(value)[:30]]
    return value


def _rows_for_prompt(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: _jsonable(value) for key, value in row.items()} for row in rows[:_MAX_ROWS]]


def _pick_tables(llm: LLM, question: str) -> list[str]:
    raw = llm.complete_json(
        system=f"{PICK}\n\n=== 表 ===\n{catalog_text()}",
        messages=[{"role": "user", "content": question}],
        schema=pick_schema(), temperature=0.2, max_tokens=1000,
    )
    names = raw.get("tables") if isinstance(raw, dict) else None
    if not isinstance(names, list):
        return []
    return expand_tables([str(name) for name in names])


def _question_text(question: str, history: list[dict[str, Any]] | None, folded_through: int = 0) -> str:
    """The question, with the raw tail of this chat when it already has some."""
    recent = "\n".join(
        f"{turn.get('role')}: {str(turn.get('content') or '')[:500]}"
        for turn in prompt_turns(history, folded_through)
        if turn.get("role") in ("user", "assistant") and str(turn.get("content") or "").strip()
    )
    if not recent:
        return question
    return f"=== 最近对话 ===\n{recent}\n\n=== 问题 ===\n{question}"


def _write_sql(
    llm: LLM, question: str, schema_text: str, *,
    context: str = "", history: list[dict[str, Any]] | None = None, folded_through: int = 0,
) -> str:
    asked = _question_text(question, history, folded_through)
    system = attach(f"{SQL}\n\n=== 结构 ===\n{schema_text}", context)
    raw = llm.complete_json(
        system=system, messages=[{"role": "user", "content": asked}],
        schema=sql_schema(), temperature=0.2, max_tokens=700,
    )
    return _strip_fence(str(raw.get("sql") or ""))


def _run(db: Database, sql: str, params: dict[str, str]) -> tuple[bool, str | None, str, list[dict[str, Any]] | None]:
    ok, message, clean = validate(sql)
    if not ok:
        # validate() returns no statement on failure. Repair and the log need the text the model wrote.
        return False, message, sql, None
    try:
        rows = db.run_scoped_read(clean, scope_views(), params)
    except Exception as err:
        return False, str(err).split("\n", 1)[0][:500], clean, None
    return True, None, clean, rows


def reflect_schema() -> dict[str, Any]:
    return {
        "name": "data_reflect", "strict": True,
        "schema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "score": {"type": "integer"},
                "retry_question": {"type": "string"},
            },
            "required": ["score", "retry_question"],
        },
    }


def answer_question(
    db: Database, llm: LLM, *, question: str,
    partnership_id: str, brand_id: str, distributor_id: str, session_id: str | None = None,
    reflect: bool = True, history: list[dict[str, Any]] | None = None,
    folded_through: int = 0, summary: str = "",
) -> QueryResult:
    """Pick tables, generate one SELECT, run it, and log the attempt.

    A failed statement is revised by codefix, up to three runs. When the rows come
    back, up to three reflections score how well they answer. A score of 80 or more
    keeps them. Below that, the reflection may ask a different question and run again.
    A reflection error keeps the latest rows that did run.
    """
    result = _attempt(
        db, llm, question=question, partnership_id=partnership_id, brand_id=brand_id,
        distributor_id=distributor_id, session_id=session_id, history=history,
        folded_through=folded_through, summary=summary,
    )
    if not reflect or not result.ok:
        return result
    chosen = result
    chosen_score = -1
    asked = question
    for turn in range(MAX_REFLECTIONS):
        try:
            score, retry = _reflect(llm, asked, result)
        except Exception:
            return result
        result.score = score
        if score >= chosen_score:
            chosen, chosen_score = result, score
        if (
            score >= REFLECT_PASS or turn == MAX_REFLECTIONS - 1
            or not retry or _same_question(retry, asked)
        ):
            return chosen
        nxt = _attempt(
            db, llm, question=retry, partnership_id=partnership_id, brand_id=brand_id,
            distributor_id=distributor_id, session_id=session_id, history=history,
            folded_through=folded_through, summary=summary,
        )
        if not nxt.ok:
            return chosen
        result = nxt
        asked = retry
    return chosen


def _attempt(
    db: Database, llm: LLM, *, question: str,
    partnership_id: str, brand_id: str, distributor_id: str, session_id: str | None,
    history: list[dict[str, Any]] | None = None, folded_through: int = 0, summary: str = "",
) -> QueryResult:
    started = time.perf_counter()
    question = (question or "").strip()[:_MAX_QUESTION]
    tables: list[str] = []
    sql: str | None = None
    ok = False
    error: str | None = None
    error_type: str | None = None
    rows: list[dict[str, Any]] = []

    try:
        if not question:
            ok, error, error_type = False, "empty question", "generation"
        else:
            tables = _pick_tables(llm, question)
            if not tables:
                ok, error, error_type = False, "no relevant tables", "generation"
            else:
                schema_text = describe_tables(tables)
                context = prompt_context(standing_block(db, partnership_id), summary_block(summary))
                sql = _write_sql(
                    llm, question, schema_text, context=context, history=history, folded_through=folded_through,
                )
                ok, error, sql, raw_rows = _run(
                    db, sql, _scope_params(partnership_id, brand_id, distributor_id),
                )
                if ok and raw_rows is not None:
                    rows = _rows_for_prompt(raw_rows)
                    error_type = None
                elif _is_validation_error(error):
                    error_type = "validation"
                else:
                    error_type = "execution"
    except Exception as err:
        ok, error, error_type = False, str(err).split("\n", 1)[0][:500], "generation"

    elapsed = int((time.perf_counter() - started) * 1000)
    log_id = _log(
        db, partnership_id=partnership_id, session_id=session_id, question=question, tables=tables,
        sql=sql, ok=ok, row_count=len(rows), elapsed=elapsed, error_type=error_type, error=error,
    )
    result = QueryResult(
        ok=ok, sql=sql, rows=rows, row_count=len(rows), error=error if not ok else None,
        error_type=error_type if not ok else None, tables=tables, log_id=log_id,
    )
    if result.ok or not (sql or "").strip():
        return result

    def run(statement: str) -> tuple[bool, QueryResult, str | None, str]:
        if not seen["done"]:
            seen["done"] = True
            return result.ok, result, result.error, ""
        nxt = run_statement(
            db, statement, question=question, partnership_id=partnership_id, brand_id=brand_id,
            distributor_id=distributor_id, session_id=session_id, tables=tables,
        )
        last["result"] = nxt
        return nxt.ok, nxt, nxt.error, ""

    seen = {"done": False}
    last = {"result": result}
    value, attempts = with_reflection(llm, kind="sql", first=sql or "", run=run)
    chosen = value if isinstance(value, QueryResult) else last["result"]
    if attempts:
        chosen.handoff = format_failures(attempts)
    return chosen


def run_statement(
    db: Database, sql: str, *, question: str,
    partnership_id: str, brand_id: str, distributor_id: str, session_id: str | None = None,
    tables: list[str] | None = None,
) -> QueryResult:
    """Run one statement the reflection step already wrote. No second model call."""
    started = time.perf_counter()
    params = _scope_params(partnership_id, brand_id, distributor_id)
    ok, error, clean, raw_rows = _run(db, sql, params)
    rows = _rows_for_prompt(raw_rows) if ok and raw_rows is not None else []
    error_type = None
    if not ok:
        error_type = "validation" if _is_validation_error(error) else "execution"
    elapsed = int((time.perf_counter() - started) * 1000)
    log_id = _log(
        db, partnership_id=partnership_id, session_id=session_id, question=question, tables=list(tables or []),
        sql=clean or sql, ok=ok, row_count=len(rows), elapsed=elapsed, error_type=error_type, error=error,
    )
    return QueryResult(
        ok=ok, sql=clean or sql, rows=rows, row_count=len(rows), error=error if not ok else None,
        error_type=error_type if not ok else None, tables=list(tables or []), log_id=log_id,
    )


def _reflect(llm: LLM, question: str, result: QueryResult) -> tuple[int, str]:
    raw = llm.complete_json(
        system=REFLECT,
        messages=[{"role": "user", "content": json.dumps({
            "question": question,
            "tables": result.tables,
            "row_count": result.row_count,
            "rows": result.rows[:20],
        }, ensure_ascii=False)}],
        schema=reflect_schema(), temperature=0, max_tokens=300,
    )
    retry = str(raw.get("retry_question") or "").strip()[:_MAX_QUESTION]
    return _clamp_score(raw.get("score")), retry


def _clamp_score(value: Any) -> int:
    try:
        score = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, score))


def _same_question(left: str, right: str) -> bool:
    return " ".join(left.lower().split()) == " ".join(right.lower().split())


def _is_validation_error(error: str | None) -> bool:
    if not error:
        return False
    if error in {
        "empty SQL", "multiple statements", "only SELECT is allowed", "could not parse SQL",
        "schema-qualified names are not allowed", "query is too deeply nested", "query is too long",
        "only one statement is allowed", "LIMIT and OFFSET must be integer constants",
    }:
        return True
    return (
        "not allowed" in error
        or error.startswith("table is not allowed")
        or error.startswith("function is not allowed")
    )


def _scope_params(partnership_id: str, brand_id: str, distributor_id: str) -> dict[str, str]:
    return {
        "partnership_id": partnership_id,
        "brand_id": brand_id,
        "distributor_id": distributor_id,
    }


def _log(
    db: Database, *, partnership_id: str, session_id: str | None, question: str, tables: list[str],
    sql: str | None, ok: bool, row_count: int, elapsed: int, error_type: str | None, error: str | None,
) -> int | None:
    try:
        return db.add_text2sql_log({
            "partnership_id": partnership_id,
            "session_id": session_id,
            "question": question,
            "selected_tables": tables,
            "generated_sql": sql,
            "execution_success": ok,
            "row_count": row_count if ok else None,
            "execution_time_ms": elapsed,
            "error_type": error_type,
            "error_message": error[:500] if error else None,
            "created_at": utcnow(),
        })
    except Exception:
        return None
