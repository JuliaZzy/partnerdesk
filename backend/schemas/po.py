"""The fixed slot template a purchase-order turn fills. Not the order row."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
    # Who settled it: the customer in chat, or the partnership's contract (pre-filled by
    # code). Shown in the draft so a contract rate reads as a given, not an offer.
    source: Literal["customer", "contract"] | None = None
    note: str | None = None  # e.g. "3rd container of the contract year → 5% (§5.01(b))"

    def describe(self, currency: str = "") -> str:
        if self.kind == "none":
            return "list price per contract" if self.source == "contract" else "no discount"
        head = f"{self.value:g}%" if self.kind == "percent" else f"{currency} {self.value:g}".strip()
        return f"contract discount {head}" if self.source == "contract" else f"{head} discount"


class PoSlots(BaseModel):
    model_config = ConfigDict(extra="ignore")

    line_items: list[LineItemSlot] = Field(default_factory=list)
    payment_terms: str | None = None
    incoterms: str | None = None
    shipping_method: str | None = None
    eta_date: str | None = None  # YYYY-MM-DD
    discount: Discount | None = None  # None = not settled by contract or customer yet
    notes: str | None = None


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
