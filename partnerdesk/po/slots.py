"""The fixed PO slot template the agent fills through conversation.

The template is KNOWN up front — it mirrors the PO create fields — so the model never
decides which values a PO needs; it only extracts the user's message into these slots.
The deterministic code-check (check.py) then decides whether the slots are enough to
build a valid, rule-compliant PO.

Which slots gate the conversation vs. which auto-default:
  - line_items ........ REQUIRED — must resolve to catalog SKUs, be priced, pass MOQ + rules.
  - discount .......... REQUIRED to ADDRESS — None means "the user hasn't said anything
                        about a discount yet"; the agent must ask (money-sensitive).
                        {"kind": "none"} once the user says they don't want one.
  - payment_terms / incoterms / shipping_method / eta_date .... have defaults; they are
                        shown in the draft table and ratified by the user's Confirm.
  - notes ............. optional.
System fields (PO number, currency, ship-to, totals) are never slots — they are derived.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..models import (
    INCOTERMS_VALUES,
    PAYMENT_TERMS_VALUES,
    SHIPPING_METHOD_VALUES,
    AgentSettings,
)


class LineItemSlot(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # The user's own wording ("fish oil", "the serum"). The model extracts this — it does
    # NOT pick a SKU. Resolution + disambiguation happen in code (check.py).
    reference: str
    # Number of CASES (not units).
    cases: int
    # Resolved catalog SKU. None until code resolves `reference`; once set it persists
    # across turns (the model keeps it) so a resolved line isn't re-searched.
    sku: str | None = None


class Discount(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: Literal["none", "amount", "percent"]
    value: float | None = None


class PoSlots(BaseModel):
    model_config = ConfigDict(extra="ignore")

    line_items: list[LineItemSlot] = Field(default_factory=list)
    payment_terms: str | None = None
    incoterms: str | None = None
    shipping_method: str | None = None
    eta_date: str | None = None  # YYYY-MM-DD
    discount: Discount | None = None  # None = not yet addressed (agent must ask)
    notes: str | None = None


def empty_slots() -> PoSlots:
    return PoSlots()


def merge_slots(prev: PoSlots, nxt: PoSlots) -> PoSlots:
    """Merge one turn's extraction over the slots already gathered.

    A None scalar coming back from the model means "the customer didn't mention this
    turn", NOT "the customer cleared it". Assigning the extraction wholesale erased any
    field the model left out of its JSON — a settled ETA or an agreed discount would
    vanish and the agent re-asked a question the user had already answered. Measured on
    llama3.2:3b: eta_date dropped on 3 of 4 follow-up turns.

    `line_items` is the deliberate exception: the prompt requires the model to return the
    full up-to-date list every turn, so it is the one field a turn may legitimately shrink.
    """
    return PoSlots(
        line_items=nxt.line_items,
        payment_terms=nxt.payment_terms if nxt.payment_terms is not None else prev.payment_terms,
        incoterms=nxt.incoterms if nxt.incoterms is not None else prev.incoterms,
        shipping_method=nxt.shipping_method if nxt.shipping_method is not None else prev.shipping_method,
        eta_date=nxt.eta_date if nxt.eta_date is not None else prev.eta_date,
        discount=nxt.discount if nxt.discount is not None else prev.discount,
        notes=nxt.notes if nxt.notes is not None else prev.notes,
    )


def default_eta_date(days: int, today: date | None = None) -> str:
    return ((today or date.today()) + timedelta(days=days)).isoformat()


class EffectiveTerms(BaseModel):
    """Slots with every defaultable term filled, so the draft always shows a concrete
    proposal. Defaults come from brand settings; anything the customer stated wins."""

    line_items: list[LineItemSlot]
    payment_terms: str
    incoterms: str
    shipping_method: str
    eta_date: str
    discount: Discount | None
    notes: str | None


def with_defaults(slots: PoSlots, settings: AgentSettings, today: date | None = None) -> EffectiveTerms:
    return EffectiveTerms(
        line_items=slots.line_items,
        payment_terms=slots.payment_terms or settings.payment_terms,
        incoterms=slots.incoterms or settings.incoterms,
        shipping_method=slots.shipping_method or settings.shipping_method,
        eta_date=slots.eta_date or default_eta_date(settings.eta_days, today),
        discount=slots.discount,
        notes=slots.notes,
    )


def enum_or_none(value: object, allowed: tuple[str, ...]) -> str | None:
    s = value.strip() if isinstance(value, str) else ""
    return s if s in allowed else None


__all__ = [
    "INCOTERMS_VALUES",
    "PAYMENT_TERMS_VALUES",
    "SHIPPING_METHOD_VALUES",
    "Discount",
    "EffectiveTerms",
    "LineItemSlot",
    "PoSlots",
    "default_eta_date",
    "empty_slots",
    "enum_or_none",
    "merge_slots",
    "with_defaults",
]
