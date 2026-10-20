"""Data Query in chat. Runs one question, then quotes the rows that came back."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from ..db import Database
from ..llm import LLM
from ..orchestrator.speak import answer_directly
from .prompt import ANSWER
from .text2sql import answer_question


def _prompt(question: str, result: Any) -> str:
    payload = {"ok": result.ok, "row_count": result.row_count, "rows": result.rows, "error": result.error}
    return (
        f"{question}\n\n"
        "=== DATA (queried from our database for this partnership; "
        "quote these figures, do not recompute or invent) ===\n"
        f"{json.dumps(payload, ensure_ascii=False)}\n"
    )


def _mark(result: Any) -> dict[str, Any]:
    return {"ok": result.ok, "row_count": result.row_count, "error": result.error, "log_id": result.log_id}


def run(
    db: Database, llm: LLM, session: dict[str, Any], text: str, question: str, *,
    partnership_id: str, brand_id: str, distributor_id: str,
) -> Iterator[dict[str, Any]]:
    db.update_session(session["id"], specialist="data")
    result = answer_question(
        db, llm, question=question,
        partnership_id=partnership_id, brand_id=brand_id,
        distributor_id=distributor_id, session_id=session["id"],
    )
    yield {"type": "data", "ok": result.ok, "row_count": result.row_count, "log_id": result.log_id}
    yield from answer_directly(
        db, llm, session, text,
        prompt_text=_prompt(text, result),
        system=ANSWER,
        extra={"data_query": _mark(result)},
    )
