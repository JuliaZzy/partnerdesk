"""Create + submit: after a human confirms a complete, rule-compliant draft, create the PO
and its lines and flip it to `submitted`. No LLM on this path. The passing code-check is
the source of resolved lines and the rule snapshot persisted with the submission.
"""

from __future__ import annotations

import random
import string
from dataclasses import dataclass
from datetime import UTC, date, datetime

from ..db import Database
from ..models import AgentSettings, Partnership, PurchaseOrder
from .check import CheckResult
from .rules import RuleResult
from .slots import PoSlots, with_defaults


@dataclass
class SubmitResult:
    po_id: str
    po_number: str
    total: float
    currency: str
    advisory: list[RuleResult]


def generate_po_number(today: date | None = None) -> str:
    d = (today or date.today()).strftime("%Y%m%d")
    tail = "".join(random.choices(string.ascii_uppercase + string.digits, k=5))
    return f"PO-{d}-{tail}"


def create_and_submit_po(
    db: Database,
    *,
    partnership: Partnership,
    settings: AgentSettings,
    slots: PoSlots,
    check: CheckResult,
    today: date | None = None,
) -> SubmitResult:
    s = with_defaults(slots, settings, today)
    subtotal = check.subtotal
    discount_amount = 0.0
    discount_pct: float | None = None
    if s.discount and s.discount.kind == "amount":
        discount_amount = float(s.discount.value or 0)
    elif s.discount and s.discount.kind == "percent":
        discount_pct = float(s.discount.value or 0)
        discount_amount = round(subtotal * discount_pct / 100, 2)
    total = round(subtotal - discount_amount, 2)
    po_id = f"po_{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}{''.join(random.choices(string.ascii_lowercase, k=4))}"
    po = PurchaseOrder(
        id=po_id, po_number=generate_po_number(today),
        brand_id=partnership.brand_id, distributor_id=partnership.distributor_id, partnership_id=partnership.id,
        status="submitted", currency=partnership.currency,
        payment_terms=s.payment_terms, incoterms=s.incoterms, shipping_method=s.shipping_method,
        eta_date=s.eta_date, ship_to_country=partnership.ship_to_country, notes=s.notes,
        discount_amount=discount_amount if discount_amount > 0 else None, discount_percentage=discount_pct,
        subtotal_amount=subtotal, total_amount=total,
        submitted_at=datetime.now(UTC), submit_cycle_version=1,
    )
    # Only resolved, priced, qty>0 lines — the code-check guaranteed these.
    items = [
        {"product_id": l.product_id, "sku": l.sku, "product_name": l.product_name, "quantity": l.cases,
         "case_price": l.case_price, "line_total": l.line_total, "case_pack": l.case_pack}
        for l in check.lines if l.resolved and l.cases > 0 and l.case_price is not None
    ]
    db.insert_po(po, items, [r.to_dict() for r in check.rule_results])
    db.add_po_event(po.id, "submitted", {"total": total, "currency": po.currency})
    return SubmitResult(po_id=po.id, po_number=po.po_number, total=total, currency=po.currency, advisory=check.advisory)
