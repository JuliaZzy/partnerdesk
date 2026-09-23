"""One turn of the PO conversation, and the confirm gate.

    extract (LLM) → merge → contract discount (code) → code-check (code, + LLM resolver fallback) → draft → reply (LLM, streamed)

The reply is streamed as `token` events; the draft table, gaps and ledger arrive in the
final `done` event — the UI renders them from code, never from the model.

Confirmation is a separate, deterministic path: no LLM, the check is re-run against the
live catalog and rules, the draft hash must equal the one the token was minted for, and
the token is burned BEFORE the order is created.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ..catalog import Catalog
from ..contracts import contract_discount_for_next_order
from ..db import Database
from ..llm import LLM
from ..memory import recall, with_memory
from ..models import AgentSettings, CommercialRule, Partnership
from .check import CheckResult, run_code_check
from .draft import build_draft, build_draft_summary, hash_draft
from .extract import extract_turn
from .reply import ReplyInput, stream_reply
from .resolver import resolve_references
from .slots import Discount, PoSlots, merge_slots
from .submit import create_and_submit_po

# A chat confirm is pressed while the draft is on screen; a day bounds a tab left open.
CONFIRM_TOKEN_TTL = timedelta(hours=24)
MAX_HISTORY_TURNS = 8


@dataclass
class Bundle:
    partnership: Partnership
    settings: AgentSettings
    catalog: Catalog
    rules: list[CommercialRule]


def load_bundle(db: Database, partnership_id: str) -> Bundle:
    p = db.partnership(partnership_id)
    if not p:
        raise KeyError(f"partnership {partnership_id} not found")
    settings = db.settings(p.brand_id)
    # Long-term memory rides in the same context slot as the brand's standing context:
    # awareness for the model, never an input to the code-check.
    memories = recall(db, partnership_id)
    if memories:
        settings = settings.model_copy(update={"operating_context": with_memory(settings.operating_context, memories)})
    return Bundle(partnership=p, settings=settings, catalog=Catalog(db.products(p.brand_id)), rules=db.rules(p.brand_id))


def build_ledger(stage: str) -> list[dict[str, Any]]:
    """Big steps (1-2-3-4) with sub-steps, reflecting the resting state after this turn."""
    sub2 = "current" if stage == "gathering" else "done"
    return [
        {"id": str(uuid.uuid4()), "title": "Understand request", "status": "done", "substeps": [
            {"title": "Determine the values this order needs", "status": "done"},
            {"title": "Fill in what you've provided", "status": "done"},
            {"title": "Check what's still missing", "status": "done"},
        ]},
        {"id": str(uuid.uuid4()), "title": "Check inputs", "status": "working" if stage == "gathering" else "done", "substeps": [
            {"title": "Minimum order quantities", "status": sub2},
            {"title": "Commercial rules", "status": sub2},
            {"title": "Pricing", "status": sub2},
        ]},
        {"id": str(uuid.uuid4()), "title": "Confirm draft", "status": "needs-you" if stage == "confirm" else "done" if stage == "submitted" else "pending"},
        {"id": str(uuid.uuid4()), "title": "Create & submit order", "status": "done" if stage == "submitted" else "pending"},
    ]


def _check(bundle: Bundle, slots: PoSlots, llm: LLM | None) -> CheckResult:
    resolve = (lambda refs: resolve_references(llm, refs, bundle.catalog.resolver_rows())) if llm else None
    check = run_code_check(
        slots=slots, catalog=bundle.catalog, rules=bundle.rules,
        partnership_id=bundle.partnership.id, ship_to_country=bundle.partnership.ship_to_country, resolve=resolve,
    )
    # Persist resolved SKUs into the slots so a resolved line isn't re-searched next turn.
    for l, li in zip(check.lines, slots.line_items, strict=True):
        if l.resolved and l.sku:
            li.sku = l.sku
    return check


def _layer_notes(check: CheckResult) -> list[str]:
    return [
        f"{l.product_name}: {l.cases} cases is a partial layer ({l.cases_per_layer}/layer) — {l.cases_to_full_layer} more fills it"
        for l in check.lines if l.partial_layer and l.cases_per_layer and l.cases_to_full_layer
    ]


def run_po_turn(
    db: Database, llm: LLM, bundle: Bundle, session: dict[str, Any], message: str, images: list[str] | None = None,
) -> Iterator[dict[str, Any]]:
    images = images or []
    current = PoSlots(**session["slots"]) if session.get("slots") else PoSlots()
    history: list[dict[str, str]] = list(session.get("history") or [])

    # 1. Extract this turn into the slot template; merge, never replace.
    try:
        ex = extract_turn(
            llm, manifest=bundle.catalog.manifest(), current=current, history=history, message=message,
            images=images, operating_context=bundle.settings.operating_context,
        )
        slots, language = merge_slots(current, ex.slots), ex.reply_language
    except Exception as err:
        yield {"type": "warning", "message": f"extraction failed: {err}"}
        slots, language = current, "English"

    # 2. The partnership's contract settles the discount before anyone is asked. Pre-filled
    #    once (source="contract"); whatever the customer says afterwards overrides it.
    applied = None
    if slots.discount is None and slots.line_items:
        cd = contract_discount_for_next_order(db, bundle.partnership.id)
        if cd:
            slots.discount = Discount(
                kind="percent" if cd.percent > 0 else "none", value=cd.percent if cd.percent > 0 else None,
                source="contract", note=cd.note,
            )
            applied = cd.note

    # 3. Deterministic gate.
    check = _check(bundle, slots, llm)
    stage = "confirm" if check.ready else "gathering"
    draft = build_draft(slots, check, bundle.partnership.currency, bundle.settings)

    # 4. Mint the confirm token only when the order is complete and compliant; bind it to
    #    the draft the user is about to see.
    token = draft_hash = expires = None
    if stage == "confirm" and draft:
        token, draft_hash = secrets.token_urlsafe(32), hash_draft(draft)
        expires = (datetime.now(UTC) + CONFIRM_TOKEN_TTL).isoformat(timespec="seconds")

    # 5. Stream the reply — told the gate outcome, so it is accurate.
    reply_text = ""
    try:
        for tok in stream_reply(llm, ReplyInput(
            tone=bundle.settings.tone, language=language, user_message=message, stage=stage,
            draft_summary=build_draft_summary(slots, check, bundle.settings, bundle.partnership.currency),
            resolutions=[(l.reference, l.product_name) for l in check.lines if l.resolved],
            gaps=check.gap_messages, advisories=[r.message for r in check.advisory],
            layer_notes=_layer_notes(check), image_count=len(images),
            operating_context=bundle.settings.operating_context, contract_discount=applied,
        )):
            reply_text += tok
            yield {"type": "token", "text": tok}
    except Exception as err:
        yield {"type": "warning", "message": f"reply failed: {err}"}

    # 6. Persist, then hand the UI the structured payload.
    history = [*history, {"role": "user", "content": message or "(image)"}, {"role": "assistant", "content": reply_text}][-MAX_HISTORY_TURNS * 2:]
    db.update_session(
        session["id"], slots=slots.model_dump(), history=history, stage=stage, specialist="po",
        confirm_token=token, confirm_hash=draft_hash, confirm_expires_at=expires,
    )
    yield {
        "type": "done", "stage": stage, "slots": slots.model_dump(), "draft": draft,
        "gaps": check.gap_messages, "gaps_detail": [g.to_dict() for g in check.gaps] + [
            {"kind": "rule", "message": r.message, "reference": None, "candidates": []} for r in check.blocking
        ],
        "advisories": [r.message for r in check.advisory], "steps": build_ledger(stage),
        "confirm_token": token, "submitted": None,
    }


def confirm_order(db: Database, bundle: Bundle, session: dict[str, Any], token: str) -> dict[str, Any]:
    """The gate. Returns {state: submitted | done | used | expired | changed, ...}."""
    if session.get("stage") == "submitted":
        po = db.po(session["po_id"]) if session.get("po_id") else None
        return {"state": "done", "po_number": po.po_number if po else None}
    stored = session.get("confirm_token")
    if not stored or stored != token:
        return {"state": "used"}
    if not session.get("confirm_expires_at") or datetime.fromisoformat(session["confirm_expires_at"]) < datetime.now(UTC):
        return {"state": "expired"}

    # Rebuild from the live catalog + live rules. No LLM: the slots were settled by the
    # conversation, and re-extracting at click time would let the model re-decide an
    # order the user has already read.
    slots = PoSlots(**session["slots"])
    check = _check(bundle, slots, llm=None)
    draft = build_draft(slots, check, bundle.partnership.currency, bundle.settings)
    if not draft or not check.ready or hash_draft(draft) != session.get("confirm_hash"):
        db.update_session(session["id"], stage="gathering", confirm_token=None, confirm_hash=None, confirm_expires_at=None)
        return {"state": "changed", "draft": draft, "gaps": check.gap_messages}

    # Burn the token BEFORE creating the order. Two clicks a second apart: one UPDATE finds
    # the token set, the other finds nothing and returns "used" without reaching create.
    if db.burn_confirm_token(token) is None:
        return {"state": "used"}
    result = create_and_submit_po(db, partnership=bundle.partnership, settings=bundle.settings, slots=slots, check=check)
    db.update_session(session["id"], stage="submitted", po_id=result.po_id)
    return {
        "state": "submitted", "po_id": result.po_id, "po_number": result.po_number, "total": result.total,
        "currency": result.currency, "advisories": [r.message for r in result.advisory], "draft": draft,
        "steps": build_ledger("submitted"),
    }
