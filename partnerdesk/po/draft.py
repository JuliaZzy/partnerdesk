"""The draft purchase order, built from code — never from the model.

One builder with three consumers that must agree exactly: the chat UI's draft table, the
summary the reply model reads, and `hash_draft`, the fingerprint the confirm token is bound
to. The hash's whole job is to prove "the order being submitted is the order the user
looked at", and that proof is worthless if the page renders one object and the hash covers
another.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

from ..models import AgentSettings
from .check import CheckResult
from .slots import PoSlots, with_defaults


def build_draft(slots: PoSlots, check: CheckResult, currency: str, settings: AgentSettings, today: date | None = None) -> dict[str, Any] | None:
    """Code-built draft table — always accurate. Terms show the brand's defaults where the
    user left them unstated. None when there is nothing to draft yet."""
    if not check.lines:
        return None
    s = with_defaults(slots, settings, today)
    discount_amount = 0.0
    if s.discount and s.discount.kind == "amount":
        discount_amount = float(s.discount.value or 0)
    elif s.discount and s.discount.kind == "percent":
        discount_amount = round(check.subtotal * float(s.discount.value or 0) / 100, 2)
    return {
        "currency": currency,
        "lines": [
            {
                "sku": l.sku, "reference": l.reference, "name": l.product_name, "cases": l.cases,
                "case_price": l.case_price, "line_total": l.line_total,
                "unresolved": l.unresolved, "ambiguous": l.ambiguous, "candidates": l.candidates,
                "below_moq": l.below_moq, "missing_price": l.missing_price, "moq_cases": l.moq_cases,
                "cases_per_layer": l.cases_per_layer, "partial_layer": l.partial_layer, "cases_to_full_layer": l.cases_to_full_layer,
            }
            for l in check.lines
        ],
        "subtotal": check.subtotal,
        "discount": s.discount.model_dump() if s.discount else None,
        "discount_amount": discount_amount,
        "total": round(check.subtotal - discount_amount, 2),
        "payment_terms": s.payment_terms,
        "incoterms": s.incoterms,
        "shipping_method": s.shipping_method,
        "eta_date": s.eta_date,
        "notes": s.notes,
    }


def build_draft_summary(slots: PoSlots, check: CheckResult, settings: AgentSettings, today: date | None = None) -> str:
    """Short natural-language summary for the reply model. Must describe the same terms
    `build_draft` renders, or the reply would talk about a different order than the one
    on screen."""
    if not check.lines:
        return "nothing yet"
    s = with_defaults(slots, settings, today)
    parts = []
    for l in check.lines:
        who = (
            f"{l.reference} (several match — needs picking)" if l.ambiguous
            else f"{l.reference} (not found)" if l.unresolved
            else l.product_name
        )
        flag = f" (below MOQ {l.moq_cases})" if l.below_moq else " (no price)" if l.missing_price else ""
        parts.append(f"{l.cases}× {who}{flag}")
    disc = (
        "discount not yet addressed" if not s.discount
        else "no discount" if s.discount.kind == "none"
        else f"{s.discount.kind} discount {s.discount.value:g}"
    )
    return f"{', '.join(parts)}. Payment {s.payment_terms}, {s.incoterms}, {s.shipping_method}, ETA {s.eta_date}, {disc}."


def hash_draft(draft: dict[str, Any]) -> str:
    """A stable fingerprint of everything a confirm click would commit.

    Only the commercially meaningful fields go in. The advisory display flags (partial
    layer, candidates, how the user worded the product) are deliberately excluded: they
    change nothing about what is bought or at what price, and including them would
    invalidate a perfectly good token because a layer hint was recalculated. Key order is
    fixed here rather than by the draft's own key order, so a reordering in `build_draft`
    can never silently change every live hash."""
    material = {
        "currency": draft["currency"],
        "lines": [
            {"sku": l["sku"], "cases": l["cases"], "case_price": l["case_price"], "line_total": l["line_total"]}
            for l in draft["lines"]
        ],
        "subtotal": draft["subtotal"],
        "discount_amount": draft["discount_amount"],
        "total": draft["total"],
        "payment_terms": draft["payment_terms"],
        "incoterms": draft["incoterms"],
        "shipping_method": draft["shipping_method"],
        "eta_date": draft["eta_date"],
        "notes": draft["notes"],
    }
    return hashlib.sha256(json.dumps(material, sort_keys=False, separators=(",", ":")).encode("utf-8")).hexdigest()
