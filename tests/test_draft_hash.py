"""The confirm hash is the entire safety argument for letting a click place an order: the
draft computed at 10:00 and the one submitted at 13:00 must be the same object. The
mirror-image guarantee matters just as much — a token must NOT be invalidated by a change
that costs the distributor nothing."""

from __future__ import annotations

from datetime import date

from partnerdesk.models import AgentSettings
from partnerdesk.po.check import CheckResult, ResolvedLine
from partnerdesk.po.draft import build_draft, hash_draft
from partnerdesk.po.slots import Discount, LineItemSlot, PoSlots

TERMS = AgentSettings()
TODAY = date(2026, 10, 1)


def line(**over) -> ResolvedLine:
    base = dict(reference="fish oil", sku="FO-100", cases=40, product_id="p-fo", product_name="Deep Sea Fish Oil",
                case_pack=12, case_price=100.0, line_total=4000.0, moq_cases=9, cases_per_layer=5)
    return ResolvedLine(**{**base, **over})


def check(lines: list[ResolvedLine]) -> CheckResult:
    return CheckResult(lines=lines, subtotal=sum(l.line_total or 0 for l in lines), gaps=[], blocking=[], advisory=[], rule_results=[], ready=True)


def slots(**over) -> PoSlots:
    # ETA pinned: `with_defaults` would otherwise derive it from today, and a hash that
    # changes at midnight is a test nobody trusts.
    base = dict(line_items=[LineItemSlot(reference="fish oil", cases=40, sku="FO-100")], discount=Discount(kind="none"), eta_date="2026-10-13")
    return PoSlots(**{**base, **over})


def h(s: PoSlots, c: CheckResult, currency="USD") -> str:
    d = build_draft(s, c, currency, TERMS, today=TODAY)
    assert d is not None
    return hash_draft(d)


BASE = h(slots(), check([line()]))


def test_same_order_hashes_the_same_twice():
    assert h(slots(), check([line()])) == BASE


def test_must_change_when_money_or_terms_move():
    assert h(slots(), check([line(case_price=110.0, line_total=4400.0)])) != BASE, "unit price"
    assert h(slots(line_items=[LineItemSlot(reference="fish oil", cases=60, sku="FO-100")]), check([line(cases=60, line_total=6000.0)])) != BASE, "quantity"
    assert h(slots(), check([line(), line(sku="SR-200", product_id="p-sr", product_name="Radiance Serum", case_price=50.0, cases=10, line_total=500.0)])) != BASE, "added line"
    assert h(slots(), check([line(sku="SR-200")])) != BASE, "swapped SKU"
    assert h(slots(discount=Discount(kind="percent", value=5)), check([line()])) != BASE, "discount"
    assert h(slots(payment_terms="net_60"), check([line()])) != BASE, "payment terms"
    assert h(slots(incoterms="DDP"), check([line()])) != BASE, "incoterms"
    assert h(slots(shipping_method="air"), check([line()])) != BASE, "shipping"
    assert h(slots(eta_date="2026-12-01"), check([line()])) != BASE, "eta"
    assert h(slots(notes="split the shipment"), check([line()])) != BASE, "notes"
    assert h(slots(), check([line()]), "EUR") != BASE, "currency"


def test_must_not_change_when_only_advisory_flags_move():
    assert h(slots(), check([line(partial_layer=True, cases_to_full_layer=2)])) == BASE, "layer hint"
    assert h(slots(), check([line(candidates=[{"sku": "FO-101", "name": "Other"}])])) == BASE, "candidates"
    assert h(slots(), check([line(reference="the fish oil one")])) == BASE, "wording"
    assert h(slots(), check([line(moq_cases=12)])) == BASE, "moq still satisfied"


def test_draft_is_none_with_no_lines():
    assert build_draft(PoSlots(), check([]), "USD", TERMS) is None
