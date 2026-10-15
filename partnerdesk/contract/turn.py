"""One chat turn that files an agreement.

The model fills the form (`read`). A filename without bytes is not a file: the desk asks
for the agreement instead of inventing terms. Event shape matches the chat stream.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ..db import Database
from ..documents import SUPPORTED_EXTS
from ..llm import LLM
from ..prompts.orchestrator import ANSWER
from .read import read_and_ingest, summarize


def run_contract_turn(
    db: Database, llm: LLM, session: dict[str, Any], documents: list[tuple[str, bytes]], text: str,
) -> Iterator[dict[str, Any]]:
    readable = [(n, b) for n, b in documents if n.lower().endswith(SUPPORTED_EXTS)]
    if not readable:
        yield from _answer(
            db, llm, session,
            text or "I want to file a distribution agreement but have not attached the file yet.",
        )
        return
    name, data = readable[0]
    try:
        rec = read_and_ingest(
            db, llm, brand_id=_brand_id(db, session["partnership_id"]), partnership_id=session["partnership_id"],
            file_name=name, data=data,
        )
    except Exception as err:
        yield from _fixed(db, session, f"I couldn't file that agreement: {err}")
        return
    reply = summarize(rec)
    if len(readable) > 1:
        reply += f" I filed {name} and left the other {len(readable) - 1} file(s) unread."
    yield from _fixed(db, session, reply, {"contract_id": rec.contract.id})


def _brand_id(db: Database, partnership_id: str) -> str:
    p = db.partnership(partnership_id)
    if not p:
        raise KeyError("partnership not found")
    return p.brand_id


def _fixed(
    db: Database, session: dict[str, Any], text: str, extra: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    history = [*(session.get("history") or []), {"role": "user", "content": "(message)"}, {"role": "assistant", "content": text}]
    db.update_session(session["id"], history=history[-16:])
    yield {"type": "token", "text": text}
    yield {
        "type": "done", "stage": session.get("stage"), "slots": session.get("slots"), "draft": None,
        "gaps": [], "gaps_detail": [], "advisories": [], "steps": [], "confirm_token": None, "submitted": None,
        **(extra or {}),
    }


def _answer(db: Database, llm: LLM, session: dict[str, Any], text: str) -> Iterator[dict[str, Any]]:
    history = list(session.get("history") or [])
    messages = [*history[-8:], {"role": "user", "content": text}]
    parts: list[str] = []
    for chunk in llm.stream_text(system=ANSWER, messages=messages):
        parts.append(chunk)
        yield {"type": "token", "text": chunk}
    reply = "".join(parts).strip() or "How can I help?"
    new_history = [*history, {"role": "user", "content": text}, {"role": "assistant", "content": reply}]
    db.update_session(session["id"], history=new_history[-16:])
    yield {
        "type": "done", "stage": session.get("stage"), "slots": session.get("slots"), "draft": None,
        "gaps": [], "gaps_detail": [], "advisories": [], "steps": [], "confirm_token": None, "submitted": None,
    }
