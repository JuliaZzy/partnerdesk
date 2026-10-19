"""Turn one natural-language question into rows from this partnership's tables.

The model picks tables, then writes one SELECT. Code parses that statement, runs it
against views that already exclude every other partnership, and returns the rows.
A later chat turn may quote those rows. It does not recompute them.

Failed and corrected attempts are stored on `text2sql_logs` for `analyze_badcase`.
Reviewed corrections are not fed back into the prompt on their own — a wrong fix
would teach the next question the same mistake.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from ..db import Database, utcnow
from ..llm import LLM
from ..prompts.data_query import PICK, REFLECT, REPAIR, SQL
from .sql_validator import validate
from .table_metadata import TABLES, catalog_text, describe_tables, expand_tables, scope_views

_MAX_QUESTION = 2_000
_MAX_CELL = 400
_MAX_ROWS = 50
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
        system=f"{PICK}\n\n=== TABLES ===\n{catalog_text()}",
        messages=[{"role": "user", "content": question}],
        schema=pick_schema(), temperature=0, max_tokens=300,
    )
    names = raw.get("tables") if isinstance(raw, dict) else None
    if not isinstance(names, list):
        return []
    return expand_tables([str(name) for name in names])


def _write_sql(llm: LLM, question: str, schema_text: str, *, previous: str | None = None, error: str | None = None) -> str:
    if previous:
        system = f"{REPAIR}\n\n=== SCHEMA ===\n{schema_text}"
        user = f"Question:\n{question}\n\nPrevious SQL:\n{previous}\n\nError:\n{(error or '')[:500]}"
    else:
        system = f"{SQL}\n\n=== SCHEMA ===\n{schema_text}"
        user = question
    raw = llm.complete_json(
        system=system, messages=[{"role": "user", "content": user}],
        schema=sql_schema(), temperature=0, max_tokens=700,
    )
    return _strip_fence(str(raw.get("sql") or ""))


def _run(db: Database, sql: str, params: dict[str, str]) -> tuple[bool, str | None, str, list[dict[str, Any]] | None]:
    ok, message, clean = validate(sql)
    if not ok:
        return False, message, clean, None
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
                "complete": {"type": "boolean"},
                "retry_question": {"type": "string"},
            },
            "required": ["complete", "retry_question"],
        },
    }


def answer_question(
    db: Database, llm: LLM, *, question: str,
    partnership_id: str, brand_id: str, distributor_id: str, session_id: str | None = None,
    reflect: bool = True,
) -> QueryResult:
    """Pick tables, generate one SELECT, run it, and log the attempt.

    A failed statement is repaired once. When the rows come back, one reflection may
    ask a different question and run again. A reflection error keeps the first result.
    """
    result = _attempt(
        db, llm, question=question, partnership_id=partnership_id, brand_id=brand_id,
        distributor_id=distributor_id, session_id=session_id,
    )
    if not reflect or not result.ok:
        return result
    try:
        complete, retry = _reflect(llm, question, result)
    except Exception:
        return result
    if complete or not retry or _same_question(retry, question):
        return result
    second = answer_question(
        db, llm, question=retry, partnership_id=partnership_id, brand_id=brand_id,
        distributor_id=distributor_id, session_id=session_id, reflect=False,
    )
    return second if second.ok else result


def _attempt(
    db: Database, llm: LLM, *, question: str,
    partnership_id: str, brand_id: str, distributor_id: str, session_id: str | None,
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
                params = _scope_params(partnership_id, brand_id, distributor_id)
                sql = _write_sql(llm, question, schema_text)
                ok, error, sql, raw_rows = _run(db, sql, params)
                if not ok:
                    sql = _write_sql(llm, question, schema_text, previous=sql, error=error)
                    ok, error, sql, raw_rows = _run(db, sql, params)
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
    return QueryResult(
        ok=ok, sql=sql, rows=rows, row_count=len(rows), error=error if not ok else None,
        error_type=error_type if not ok else None, tables=tables, log_id=log_id,
    )


def _reflect(llm: LLM, question: str, result: QueryResult) -> tuple[bool, str]:
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
    return bool(raw.get("complete")), retry


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
