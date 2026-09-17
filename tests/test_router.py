from __future__ import annotations

from partnerdesk.llm import FakeLLM
from partnerdesk.router import TOPIC_KEYS, classify, route


def cls(topics, **extra):
    return {"topics": [{"topic": k, "confidence": c} for k, c in topics], "requests": [], "summary": "", **extra}


def test_active_po_keeps_every_message_without_calling_the_model():
    llm = FakeLLM()
    r = route(llm, message="use the hydrating toner", attachments=[], history=[], active_specialist="po", stage="gathering")
    assert r.specialist == "po" and llm.calls == []


def test_purchase_order_routes_to_po():
    llm = FakeLLM(json_replies=[cls([("purchase_order", 0.9)])])
    assert route(llm, message="20 cases of fish oil", attachments=[], history=[], active_specialist=None, stage=None).specialist == "po"


def test_po_confidence_floor_demotes_a_shaky_order():
    llm = FakeLLM(json_replies=[cls([("purchase_order", 0.4)])])
    assert route(llm, message="maybe order?", attachments=[], history=[], active_specialist=None, stage=None, min_po_confidence=0.5).specialist is None


def test_empty_topics_abstain():
    llm = FakeLLM(json_replies=[cls([])])
    r = route(llm, message="hello!", attachments=[], history=[], active_specialist=None, stage=None)
    assert r.specialist is None


def test_contract_and_report_need_a_document():
    llm = FakeLLM(json_replies=[cls([("contract_terms", 0.9)]), cls([("contract_terms", 0.9)]), cls([("sales_reporting", 0.8)])])
    assert route(llm, message="about our agreement", attachments=[], history=[], active_specialist=None, stage=None).specialist is None
    assert route(llm, message="here is the agreement", attachments=["agreement.pdf"], history=[], active_specialist=None, stage=None).specialist == "contract"
    assert route(llm, message="monthly report attached", attachments=["sept.xlsx"], history=[], active_specialist=None, stage=None).specialist == "report"


def test_multi_label_prefers_the_higher_confidence_specialist():
    llm = FakeLLM(json_replies=[cls([("sales_reporting", 0.6), ("purchase_order", 0.9)])])
    assert route(llm, message="report attached, also reorder 20 cases", attachments=["sept.xlsx"], history=[], active_specialist=None, stage=None).specialist == "po"


def test_classify_drops_unknown_keys_and_clamps_confidence():
    llm = FakeLLM(json_replies=[cls([("purchase_order", 1.7), ("made_up", 0.9)], requests=["Send price list", 3])])
    c = classify(llm, message="x", attachments=[], history=[])
    assert c.topics == [("purchase_order", 1.0)] and c.requests == ["Send price list"]
    assert set(llm.calls[0]["schema"]["schema"]["properties"]["topics"]["items"]["properties"]["topic"]["enum"]) == set(TOPIC_KEYS)
