"""The agent's operations, independent of transport.

`handle_message` yields the same event stream whether it is consumed by the HTTP API
(app.py, as SSE) or the Streamlit page (ui.py, in-process). Confirmation is a plain call.
Nothing in here knows about requests, responses or widgets.

Events:  {type: route}  {type: token, text}  {type: warning, message}  {type: done, …}
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

from .claims import handle_post_submit
from .db import Database
from .llm import LLM
from .po.turn import build_ledger, confirm_order, load_bundle, run_po_turn
from .router import route

MAX_IMAGES = 3

ABSTAIN_REPLY = (
    "That's not something I can act on here — I'll leave it for a person at the brand. "
    "If you'd like to place an order, just tell me the products and case counts."
)
CONTRACT_STUB = "I can see this is about the agreement. Contract reading isn't wired into chat yet — a person on the brand side will pick this up."
REPORT_STUB = "Thanks for the report. Report ingestion isn't wired into chat yet — a person on the brand side will pick this up."


def new_session(db: Database, partnership_id: str | None = None) -> dict[str, Any]:
    pid = partnership_id or next((p.id for p in db.partnerships()), None)
    if not pid or not db.partnership(pid):
        raise KeyError("partnership not found")
    return db.create_session(uuid.uuid4().hex, pid)


def _one_shot(db: Database, session: dict[str, Any], text: str, extra: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
    """A reply composed in code, delivered in the same event shape as a streamed one."""
    history = [*(session.get("history") or []), {"role": "user", "content": "(message)"}, {"role": "assistant", "content": text}]
    db.update_session(session["id"], history=history[-16:])
    yield {"type": "token", "text": text}
    yield {
        "type": "done", "stage": session.get("stage"), "slots": session.get("slots"), "draft": None,
        "gaps": [], "gaps_detail": [], "advisories": [], "steps": [], "confirm_token": None, "submitted": None,
        **(extra or {}),
    }


def handle_message(
    db: Database, llm: LLM, session_id: str, text: str, images: list[str] | None = None, attachments: list[str] | None = None,
) -> Iterator[dict[str, Any]]:
    session = db.session(session_id)
    if not session:
        raise KeyError("session not found")
    text = (text or "").strip()
    images = [u for u in (images or []) if isinstance(u, str) and u.startswith("data:image/")][:MAX_IMAGES]
    if not text and not images:
        raise ValueError("message required")
    bundle = load_bundle(db, session["partnership_id"])
    names = [*(attachments or []), *[f"image-{i + 1}.jpg" for i in range(len(images))]]

    r = route(
        llm, message=text, attachments=names, history=session.get("history") or [],
        active_specialist=session.get("specialist"), stage=session.get("stage"),
        min_po_confidence=bundle.settings.min_po_confidence,
    )
    yield {"type": "route", "specialist": r.specialist, "reason": r.reason}

    if r.specialist == "po":
        # An order already placed in this session: receipt / payment / claim first.
        if session.get("stage") == "submitted" and session.get("po_id"):
            po = db.po(session["po_id"])
            if po:
                outcome = handle_post_submit(db, llm, po, text)
                if outcome.kind != "none":
                    yield from _one_shot(db, session, outcome.message, {"po_event": outcome.kind})
                    return
                if outcome.message:
                    yield from _one_shot(db, session, outcome.message)
                    return
            # Not about the placed order → a fresh one in the same chat.
            db.update_session(session_id, slots={}, stage="gathering", po_id=None, confirm_token=None, confirm_hash=None)
            session = db.session(session_id) or session
        yield from run_po_turn(db, llm, bundle, session, text, images)
    elif r.specialist == "contract":
        db.update_session(session_id, specialist="contract")
        yield from _one_shot(db, session, CONTRACT_STUB)
    elif r.specialist == "report":
        db.update_session(session_id, specialist="report")
        yield from _one_shot(db, session, REPORT_STUB)
    else:
        yield from _one_shot(db, session, ABSTAIN_REPLY)


def confirm(db: Database, session_id: str, token: str) -> dict[str, Any]:
    """The gate. Adds a code-composed `message` for the UI on the two states a person reads."""
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
        db.update_session(session_id, history=history[-16:])
        result["message"] = msg
    elif result["state"] == "changed":
        result["message"] = "The order moved since you last saw it (a price or rule changed), so I didn't place it. Here's the current draft — please check it again."
        result["steps"] = build_ledger("gathering")
    return result


def orders(db: Database, partnership_id: str | None = None) -> list[dict[str, Any]]:
    return [{**po.model_dump(), "items": db.po_items(po.id), "events": db.po_events(po.id)} for po in db.list_pos(partnership_id)]
