from __future__ import annotations

from partnerdesk.models import Product
from partnerdesk.po.rules import EvalItem, POContext, evaluate_rules, is_rule_live, moq_minimum_cases
from tests.conftest import rule


def item(pid="p-fo", sku="FO-100", qty=10, price=100.0, role=None, discontinued=False, tags=(), case_pack=12) -> EvalItem:
    p = Product(id=pid, sku=sku, product_name_en=sku, case_pack=case_pack, case_price=price, commercial_role=role, discontinued=discontinued, policy_tags=list(tags))
    return EvalItem(product_id=pid, sku=sku, product_name=sku, quantity=qty, case_pack=case_pack, case_price=price, line_total=price * qty, product=p)


def one(rules, items, po=POContext(ship_to_country="US", partnership_id="ps-1")):
    return evaluate_rules(po, items, rules, today="2026-06-01")


def test_lifecycle_window_is_inclusive_and_paused_wins():
    assert is_rule_live(rule(effective_from="2026-06-01", effective_until="2026-06-30"), "2026-06-01")
    assert is_rule_live(rule(effective_from="2026-06-01", effective_until="2026-06-30"), "2026-06-30")
    assert not is_rule_live(rule(effective_from="2026-06-02"), "2026-06-01")
    assert not is_rule_live(rule(effective_until="2026-05-31"), "2026-06-01")
    assert not is_rule_live(rule(is_active=False), "2026-06-01")


def test_partner_scope_skips_the_rule_entirely_not_just_passes():
    r = rule(partner_scope="specific", partner_ids=["someone-else"], rule_config={"min": 99999})
    assert one([r], [item()]) == []
    r2 = rule(partner_scope="region", partner_regions=["us"], rule_config={"min": 99999})
    assert one([r2], [item()])[0].triggered


def test_min_order_value():
    res = one([rule(rule_config={"min": 1500})], [item(qty=10)])
    assert res[0].triggered and "$500.00 below" in res[0].message
    assert not one([rule(rule_config={"min": 1000})], [item(qty=10)])[0].triggered


def test_gift_pct_cap_counts_only_gift_role():
    r = rule(rule_type="gift_pct_cap", rule_config={"maxPct": 10})  # camelCase accepted
    res = one([r], [item(), item(pid="p-gs", sku="GS-010", qty=5, price=40.0, role="gift")])
    assert res[0].triggered and res[0].affected_items[0]["sku"] == "GS-010"
    assert not one([r], [item(), item(pid="p-gs", sku="GS-010", qty=2, price=40.0, role="gift")])[0].triggered


def test_discount_approval_uses_po_level_pct():
    r = rule(rule_type="discount_approval", rule_config={"threshold_pct": 15}, severity="approval_required")
    assert one([r], [item()], POContext(discount_percentage=15.0))[0].triggered
    assert not one([r], [item()], POContext(discount_percentage=14.9))[0].triggered


def test_territory_restriction():
    r = rule(rule_type="territory_restriction", rule_config={"territories": ["kp", "IR"]})
    assert one([r], [item()], POContext(ship_to_country="ir"))[0].triggered
    assert not one([r], [item()], POContext(ship_to_country="SE"))[0].triggered
    assert not one([r], [item()], POContext(ship_to_country=None))[0].triggered


def test_discontinued_block_and_block_all_with_product_scope():
    old = item(pid="p-old", sku="OLD-900", discontinued=True)
    assert one([rule(rule_type="discontinued_block")], [item(), old])[0].triggered
    r = rule(rule_type="block_all", product_scope="specific", product_ids=["p-old"])
    assert one([r], [item(), old])[0].affected_items == [{"product_name": "OLD-900", "sku": "OLD-900", "quantity": 10}]
    assert not one([r], [item()])[0].triggered


def test_unit_cap_case_mode_by_default_and_unit_mode_multiplies_by_case_pack():
    r = rule(rule_type="unit_cap", rule_config={"max_units": 24}, product_scope="tag", product_tags=["restricted"])
    it = item(qty=3, tags=("restricted",), case_pack=12)
    assert not one([r], [it])[0].triggered  # 3 cases ≤ 24
    r_units = rule(rule_type="unit_cap", rule_config={"max_units": 24, "unit": "unit"}, product_scope="tag", product_tags=["restricted"])
    assert one([r_units], [it])[0].triggered  # 36 units > 24


def test_value_pct_cap_is_against_the_whole_po():
    r = rule(rule_type="value_pct_cap", rule_config={"max_pct": 10}, product_scope="role", product_roles=["sample"])
    sample = item(pid="p-sm", sku="SM-001", qty=30, price=5.0, role="sample")  # 150 of 1150 = 13%
    assert one([r], [item(), sample])[0].triggered


def test_unknown_rule_type_is_ignored():
    assert one([rule(rule_type="does_not_exist")], [item()]) == []


def test_moq_minimum_cases_rounds_up_and_guards_zero():
    assert moq_minimum_cases(100, 12) == 9
    assert moq_minimum_cases(96, 12) == 8
    assert moq_minimum_cases(None, 12) is None and moq_minimum_cases(10, 0) is None
