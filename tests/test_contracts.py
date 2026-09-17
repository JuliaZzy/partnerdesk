from __future__ import annotations

from partnerdesk.contracts import apply_to_db, rules_from_extraction
from partnerdesk.po.check import run_code_check
from partnerdesk.po.slots import Discount, LineItemSlot, PoSlots

EXTRACTION = {
    "minimum_order_quantities": [
        {"scope": "per_order", "quantity": 2000, "currency": "USD"},
        {"sku": "FO-100", "quantity": 240, "unit": "units"},
        {"scope": "per_shipment", "quantity": 1, "unit": "container"},  # ambiguous → skipped
    ],
    "excluded_countries": ["kp", "IR"],
    "commercial_discount_rules": [{"bands": [{"from_container": 1, "discount_pct": 5}, {"from_container": 3, "discount_pct": 8}]}],
}


def test_mapping_produces_rules_moqs_and_reports_ambiguity():
    r = rules_from_extraction(EXTRACTION, brand_id="brand-1", partnership_id="ps-1")
    by_type = {x.rule_type: x for x in r.rules}
    assert by_type["min_order_value"].rule_config == {"min": 2000.0} and by_type["min_order_value"].severity == "block"
    assert by_type["territory_restriction"].rule_config == {"territories": ["KP", "IR"]}
    assert by_type["discount_approval"].rule_config["threshold_pct"] == 8.01 and by_type["discount_approval"].severity == "approval_required"
    assert all(x.partner_scope == "specific" and x.partner_ids == ["ps-1"] and "(from contract)" in x.name for x in r.rules)
    assert r.product_moq_units == {"FO-100": 240}
    assert len(r.skipped) == 1 and "ambiguous" in r.skipped[0]


def test_contract_terms_gate_the_next_order(db):
    r = rules_from_extraction(EXTRACTION, brand_id="brand-1", partnership_id="ps-1")
    apply_to_db(db, "brand-1", r)
    from partnerdesk.catalog import Catalog
    catalog, rules = Catalog(db.products("brand-1")), db.rules("brand-1")
    # 10 cases × $100 = $1000 < $2000 contract minimum, and 10 cases < 240/12 = 20 MOQ.
    slots = PoSlots(line_items=[LineItemSlot(reference="fish oil", cases=10, sku=None)], discount=Discount(kind="none"))
    c = run_code_check(slots=slots, catalog=catalog, rules=rules, partnership_id="ps-1", ship_to_country="US")
    assert c.ready is False and c.lines[0].moq_cases == 20 and any(g.kind == "below_moq" for g in c.gaps)
    slots = PoSlots(line_items=[LineItemSlot(reference="fish oil", cases=20, sku=None)], discount=Discount(kind="none"))
    c = run_code_check(slots=slots, catalog=catalog, rules=rules, partnership_id="ps-1", ship_to_country="US")
    assert c.ready is True  # 20 × 100 = 2000 meets the minimum
    c = run_code_check(slots=slots, catalog=catalog, rules=rules, partnership_id="ps-1", ship_to_country="IR")
    assert c.ready is False and c.blocking[0].rule_type == "territory_restriction"
