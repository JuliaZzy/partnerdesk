"""聊天里的报表。一份草稿：查询和新闻留在 `turn`，这个模块把它流式送出。"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

from ..db import Database
from ..llm import LLM
from ..orchestrator.speak import save_reply
from .turn import ReportDraft, iter_draft


def run(
    db: Database, llm: LLM, session: dict[str, Any], text: str, *,
    partnership_id: str, brand_id: str, distributor_id: str, hint: str | None,
) -> Iterator[dict[str, Any]]:
    db.update_session(session["id"], specialist="report")
    draft: ReportDraft | None = None
    for item in iter_draft(
        db, llm, message=text, history=list(session.get("history") or []),
        partnership_id=partnership_id, brand_id=brand_id, distributor_id=distributor_id,
        session_id=session["id"], hint=hint,
        folded_through=int(session.get("folded_through") or 0), summary=str(session.get("summary") or ""),
    ):
        if isinstance(item, ReportDraft):
            draft = item
        else:
            yield item
    if draft is None:
        draft = ReportDraft(text="The report draft is empty.", charts=[], web_notes=[], warnings=["draft failed"], data_query=None)
    for warning in draft.warnings:
        yield {"type": "warning", "message": warning}
    reply = draft.text
    yield {"type": "token", "text": reply}
    extra: dict[str, Any] = {"report_draft": True, "report_pending_id": f"rp_{uuid.uuid4().hex[:12]}"}
    if draft.charts:
        extra["charts"] = draft.charts
    if draft.series:
        extra["report_series"] = draft.series
    if draft.web_notes:
        extra["web_notes"] = draft.web_notes
    if draft.data_query:
        extra["data_query"] = draft.data_query
    if draft.title:
        extra["report_title"] = draft.title
    yield save_reply(db, session, text, reply, extra, llm=llm)
