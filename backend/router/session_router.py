"""Chat: open a session, stream one turn, confirm an order, rate a data answer."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from .. import service
from ..db import Database
from ..deps import get_db, get_llm, llm_for
from ..llm import LLM
from ..schemas import Confirm, DataFeedback, Inbound, InboundFile, NewSession
from ._common import partnership_or_404

router = APIRouter()

_MAX_FILE_BYTES = 12 * 1024 * 1024


def _decode_file(f: InboundFile) -> tuple[str, bytes]:
    raw = f.data.split(",", 1)[-1] if f.data.startswith("data:") else f.data
    try:
        data = base64.b64decode(raw, validate=True)
    except Exception as err:
        raise HTTPException(400, f"could not read {f.name}") from err
    if len(data) > _MAX_FILE_BYTES:
        raise HTTPException(400, f"{f.name} is larger than 12 MB")
    return f.name, data


@router.get("/api/sessions")
def list_sessions(partnership_id: str, db: Database = Depends(get_db)) -> list[dict[str, Any]]:
    partnership_or_404(db, partnership_id)
    rows = db.list_sessions(partnership_id)
    keep = ("id", "partnership_id", "title", "tags", "stage", "updated_at", "created_at")
    return [{k: row.get(k) for k in keep} for row in rows]


@router.post("/api/sessions")
def new_session(body: NewSession, db: Database = Depends(get_db)) -> dict[str, Any]:
    try:
        return service.new_session(db, body.partnership_id)
    except KeyError as err:
        raise HTTPException(404, str(err)) from err


@router.get("/api/sessions/{sid}")
def get_session(sid: str, db: Database = Depends(get_db)) -> dict[str, Any]:
    s = db.session(sid)
    if not s:
        raise HTTPException(404, "session not found")
    return s


@router.post("/api/sessions/{sid}/message")
def message(sid: str, body: Inbound, db: Database = Depends(get_db), llm: LLM = Depends(get_llm)) -> StreamingResponse:
    llm = llm_for(llm, body.model)
    if not db.session(sid):
        raise HTTPException(404, "session not found")
    if not body.message.strip() and not body.images and not body.files:
        raise HTTPException(400, "message required")
    documents = [_decode_file(f) for f in body.files]

    def gen() -> Iterator[bytes]:
        for e in service.handle_message(db, llm, sid, body.message, body.images, body.attachments, documents):
            yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n".encode()

    return StreamingResponse(
        gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/sessions/{sid}/confirm")
def confirm(sid: str, body: Confirm, db: Database = Depends(get_db)) -> dict[str, Any]:
    try:
        return service.confirm(db, sid, body.token)
    except KeyError as err:
        raise HTTPException(404, str(err)) from err


@router.post("/api/sessions/{sid}/data-feedback")
def data_feedback(sid: str, body: DataFeedback, db: Database = Depends(get_db)) -> dict[str, Any]:
    """Rate the query behind one assistant turn. The log must belong to this chat."""
    session = db.session(sid)
    if not session:
        raise HTTPException(404, "session not found")
    try:
        db.set_text2sql_feedback(
            body.log_id, body.feedback, reason=body.reason, corrected_sql=body.corrected_sql,
            partnership_id=session["partnership_id"], session_id=sid,
        )
    except KeyError as err:
        raise HTTPException(404, "query log not found") from err
    except ValueError as err:
        raise HTTPException(400, str(err)) from err
    db.stamp_text2sql_feedback(sid, body.log_id, body.feedback)
    return {"ok": True, "feedback": body.feedback}
