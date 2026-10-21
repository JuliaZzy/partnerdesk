"""The agent's operations, independent of transport.

Chat turns live in `orchestrator.handle_message` (the same event stream the HTTP API
serializes for the web desk). Confirmation is a plain call. Nothing in here knows about
requests, responses or widgets.

Events:  {type: route}  {type: token, text}  {type: warning, message}  {type: done, …}
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

from .db import Database
from .llm import LLM
from .memory import record_history
from .orchestrator import MAX_IMAGES
from .orchestrator.turn import handle_message as run_turn
from .po.turn import build_ledger, confirm_order, load_bundle
from .turn_lock import hold_session

__all__ = ["MAX_IMAGES", "confirm", "delete_session", "handle_message", "new_session", "orders"]


def new_session(db: Database, partnership_id: str | None = None) -> dict[str, Any]:
    pid = partnership_id or next((p.id for p in db.partnerships()), None)
    if not pid or not db.partnership(pid):
        raise KeyError("partnership not found")
    return db.create_session(uuid.uuid4().hex, pid)


def handle_message(
    db: Database, llm: LLM, session_id: str, text: str, images: list[str] | None = None,
    attachments: list[str] | None = None, documents: list[tuple[str, bytes]] | None = None,
) -> Iterator[dict[str, Any]]:
    """One turn. A second send on this chat waits; other chats do not."""
    with hold_session(session_id):
        yield from run_turn(db, llm, session_id, text, images, attachments, documents)


def delete_session(db: Database, session_id: str) -> None:
    db.delete_session(session_id)


def confirm(db: Database, session_id: str, token: str) -> dict[str, Any]:
    """The gate. Adds a code-composed `message` for the UI on the two states a person reads."""
    with hold_session(session_id):
        return _confirm(db, session_id, token)


def _confirm(db: Database, session_id: str, token: str) -> dict[str, Any]:
    session = db.session(session_id)
    if not session:
        raise KeyError("session not found")
    bundle = load_bundle(db, session["partnership_id"])
    result = confirm_order(db, bundle, session, token)
    if result["state"] == "submitted":
        msg = f"Done — placed as {result['po_number']} ({result['currency']} {result['total']:.2f}). The brand will review it next."
        if result["advisories"]:
            msg += " It needs their approval because: " + "; ".join(result["advisories"])
        history = [*(session.get("history") or []), {"role": "user", "content": "(confirm)"}, {"role": "assistant", "content": msg}]
        record_history(db, session, history)
        result["message"] = msg
    elif result["state"] == "changed":
        result["message"] = "The order moved since you last saw it (a price or rule changed), so I didn't place it. Here's the current draft — please check it again."
        result["steps"] = build_ledger("gathering")
    return result


def orders(db: Database, partnership_id: str | None = None) -> list[dict[str, Any]]:
    return [{**po.model_dump(), "items": db.po_items(po.id), "events": db.po_events(po.id)} for po in db.list_pos(partnership_id)]
