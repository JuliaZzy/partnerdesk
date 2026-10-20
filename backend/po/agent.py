"""PO Agent. One chat turn: a claim on a submitted order, or the order conversation.

Pricing, MOQ, and commercial rules stay in `check` and `rules`. This module
only chooses which path this message takes.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ..db import Database
from ..llm import LLM
from ..orchestrator.speak import one_shot
from .claims import handle_post_submit
from .turn import Bundle, run_po_turn


def run(
    db: Database, llm: LLM, session: dict[str, Any], text: str, images: list[str], bundle: Bundle,
) -> Iterator[dict[str, Any]]:
    session_id = session["id"]
    if session.get("stage") == "submitted" and session.get("po_id"):
        po = db.po(session["po_id"])
        if po:
            outcome = handle_post_submit(db, llm, po, text)
            if outcome.kind != "none":
                yield from one_shot(db, session, outcome.message, {"po_event": outcome.kind})
                return
            if outcome.message:
                yield from one_shot(db, session, outcome.message)
                return
        db.update_session(
            session_id, slots={}, stage="gathering", po_id=None, confirm_token=None, confirm_hash=None,
        )
        session = db.session(session_id) or session
    db.update_session(session_id, specialist="po")
    yield from run_po_turn(db, llm, bundle, session, text, images)
