"""AI Report in chat. One draft: queries and news stay in `turn`, this module streams it."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ..db import Database
from ..llm import LLM
from ..orchestrator.speak import save_reply
from .turn import draft_report


def run(
    db: Database, llm: LLM, session: dict[str, Any], text: str, *,
    partnership_id: str, brand_id: str, distributor_id: str, hint: str | None,
) -> Iterator[dict[str, Any]]:
    db.update_session(session["id"], specialist="report")
    draft = draft_report(
        db, llm, message=text, history=list(session.get("history") or []),
        partnership_id=partnership_id, brand_id=brand_id, distributor_id=distributor_id,
        session_id=session["id"], hint=hint,
    )
    for warning in draft.warnings:
        yield {"type": "warning", "message": warning}
    reply = draft.text
    yield {"type": "token", "text": reply}
    extra: dict[str, Any] = {"report_draft": True}
    if draft.charts:
        extra["charts"] = draft.charts
    if draft.web_notes:
        extra["web_notes"] = draft.web_notes
    if draft.data_query:
        extra["data_query"] = draft.data_query
    yield save_reply(db, session, text, reply, extra)
