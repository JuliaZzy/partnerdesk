"""Slot merge: an omitted field is unchanged, never cleared. The first version assigned the
extraction wholesale, so any slot the model left out silently reverted to None and the agent
re-asked a settled question (llama3.2:3b: eta_date dropped on 3 of 4 follow-up turns)."""

from __future__ import annotations

from datetime import date

from partnerdesk.models import AgentSettings
from partnerdesk.po.extract import normalize_slots
from partnerdesk.po.slots import Discount, LineItemSlot, PoSlots, merge_slots, with_defaults

SETTLED = PoSlots(
    line_items=[LineItemSlot(reference="fish oil", cases=20, sku="FO-100")],
    payment_terms="net_30", incoterms="FOB", shipping_method="air",
    eta_date="2026-11-15", discount=Discount(kind="percent", value=5), notes="Rotterdam warehouse",
)


def test_omitted_fields_keep_settled_values():
    sparse = PoSlots(line_items=SETTLED.line_items)
    m = merge_slots(SETTLED, sparse)
    assert (m.eta_date, m.payment_terms, m.incoterms, m.shipping_method, m.notes) == ("2026-11-15", "net_30", "FOB", "air", "Rotterdam warehouse")
    assert m.discount and m.discount.kind == "percent"


def test_a_stated_change_wins_and_none_is_a_change():
    m = merge_slots(SETTLED, PoSlots(line_items=SETTLED.line_items, shipping_method="sea", discount=Discount(kind="none")))
    assert m.shipping_method == "sea" and m.discount and m.discount.kind == "none" and m.eta_date == "2026-11-15"


def test_line_items_follow_the_turn_but_terms_survive():
    m = merge_slots(SETTLED, PoSlots(line_items=[]))
    assert m.line_items == [] and m.payment_terms == "net_30"


def test_with_defaults_fills_only_what_is_unstated():
    t = with_defaults(PoSlots(incoterms="DDP"), AgentSettings(eta_days=10), today=date(2026, 1, 1))
    assert (t.payment_terms, t.incoterms, t.shipping_method, t.eta_date) == ("50_50", "DDP", "sea", "2026-01-11")


# --- model output is never trusted -------------------------------------------

def test_normalize_rejects_bad_enums_dates_and_quantities():
    s = normalize_slots({
        "line_items": [{"reference": "fish oil", "cases": "10.7", "sku": " FO-100 "}, {"reference": "", "cases": 5, "sku": None}, {"reference": "x", "cases": 0, "sku": None}, "junk"],
        "payment_terms": "net_45", "incoterms": "fob", "shipping_method": "sea", "eta_date": "next tuesday",
        "discount_kind": "percent", "discount_value": "abc", "notes": "  ",
    })
    assert [(l.reference, l.cases, l.sku) for l in s.line_items] == [("fish oil", 10, "FO-100")]
    assert s.payment_terms is None and s.incoterms is None and s.shipping_method == "sea"
    assert s.eta_date is None and s.discount is None and s.notes is None


def test_normalize_discount_three_states():
    assert normalize_slots({"discount_kind": None}).discount is None
    # What the customer says is marked as theirs — it outranks a contract pre-fill.
    assert normalize_slots({"discount_kind": "none"}).discount == Discount(kind="none", source="customer")
    assert normalize_slots({"discount_kind": "amount", "discount_value": 50}).discount == Discount(kind="amount", value=50, source="customer")
    assert normalize_slots({"discount_kind": "percent", "discount_value": -3}).discount is None


def test_echoed_contract_rate_keeps_its_provenance():
    prev = PoSlots(discount=Discount(kind="percent", value=3, source="contract", note="1st container"))
    echoed = merge_slots(prev, PoSlots(discount=Discount(kind="percent", value=3, source="customer")))
    assert echoed.discount is prev.discount
    changed = merge_slots(prev, PoSlots(discount=Discount(kind="percent", value=5, source="customer")))
    assert changed.discount.source == "customer" and changed.discount.value == 5
    dropped = merge_slots(prev, PoSlots(discount=Discount(kind="none", source="customer")))
    assert dropped.discount.kind == "none" and dropped.discount.source == "customer"
