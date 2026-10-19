"""An agreement file is read into the extraction the contract tables already store.
The model fills the form; a quote that is not in the text is dropped; filing writes
the contract. A chat attachment that is only a name does not pretend to have the file.
"""

from __future__ import annotations

import pytest

from backend import service
from backend.contract import read_agreement, read_and_ingest, sanitize_extraction
from backend.llm import FakeLLM

AGREEMENT = (
    "Distribution agreement.\n\n"
    "Term 2026-01-01 to 2027-12-31. Territory: Sweden.\n\n"
    "Each purchase order shall have a minimum value of USD 500.\n\n"
    "In each contract year the first and second containers are invoiced at list price less 3%."
)

EXTRACTION = {
    "contract_type": "distribution_agreement",
    "effective_date": "2026-01-01",
    "term_start_date": "2026-01-01",
    "term_end_date": "2027-12-31",
    "auto_renewal": False,
    "exclusivity_type": None,
    "territory_text": "Sweden.",
    "allowed_countries": ["se", "Sweden"],
    "excluded_countries": [],
    "minimum_order_quantities": [
        {"quantity": 500, "unit": "USD", "applies_per": "per_order", "source_clause_text": "minimum value of USD 500"},
    ],
    "commercial_discount_rules": [{
        "title": "Container sequence",
        "section_reference": "5",
        "applies_per": "each_contract_year",
        "basis": "shipping_container_sequence",
        "source_clause_text": "the first and second containers are invoiced at list price less 3%",
        "tiers": [{"from_container": 1, "to_container": 2, "discount_percent": 3, "notes": None}],
    }],
    "product_price_list": [],
    "field_evidence": {
        "minimum_order_quantities": [
            {"page": 1, "quote": "minimum value of USD 500", "section_hint": "4"},
            {"page": 1, "quote": "the model invented this sentence", "section_hint": None},
        ],
    },
    "confidence_score": 0.8,
}


def test_sanitize_keeps_only_quotes_that_are_in_the_document():
    pages = [(1, AGREEMENT)]
    out = sanitize_extraction(EXTRACTION, pages, set())
    assert out["allowed_countries"] == ["SE"]
    assert out["term_start_date"] == "2026-01-01"
    quotes = [e["quote"] for e in out["field_evidence"]["minimum_order_quantities"]]
    assert quotes == ["minimum value of USD 500"]


def test_read_and_file_an_agreement(db):
    llm = FakeLLM(json_replies=[dict(EXTRACTION)])
    rec = read_and_ingest(db, llm, brand_id="brand-1", partnership_id="ps-1", file_name="agreement.txt", data=AGREEMENT.encode(), title="Nordic 2026")
    assert rec.contract.title == "Nordic 2026" and rec.terms.discount_rules[0].tiers[0].discount_percent == 3
    assert len(rec.terms.evidence) == 1
    stored = db.active_contract("ps-1")
    assert stored and stored.id == rec.contract.id and stored.file_name == "agreement.txt"
    content = llm.calls[0]["messages"][0]["content"]
    assert "Section path: preamble" in content or "Section id: c1" in content
    assert db.contract_chunks(rec.contract.id)


_EMPTY = {
    "commercial_discount_rules": [],
    "minimum_order_quantities": [],
    "product_price_list": [],
    "allowed_countries": [],
    "excluded_countries": [],
    "field_evidence": {},
}


def test_the_model_picks_chunks_from_the_full_outline():
    text = "第一条 定义\n术语以本合同为准。\n第二条 订货\n最低订单金额为 USD 500。\n"
    llm = FakeLLM(json_replies=[{"selected_ids": ["c2"]}, dict(EXTRACTION), {"selected_ids": []}])
    read_agreement(llm, "agreement.txt", text.encode())
    select, extract, reflect = llm.calls
    assert select["schema"]["name"] == "contract_chunk_select"
    outline = select["messages"][0]["content"]
    assert "第一条" in outline and "定义" in outline and "第二条" in outline and "订货" in outline
    assert "术语以本合同为准" not in outline
    body = extract["messages"][0]["content"]
    assert "USD 500" in body and "术语以本合同为准" not in body
    retry = reflect["messages"][0]["content"]
    assert "missing_fields" in retry and "定义" in retry and "订货" not in retry


def test_high_confidence_skips_another_pass():
    text = "第一条 定义\n术语以本合同为准。\n第二条 订货\n最低订单金额为 USD 500。\n"
    high = {**EXTRACTION, "confidence_score": 0.9}
    llm = FakeLLM(json_replies=[{"selected_ids": ["c2"]}, high, {"selected_ids": ["c1"]}])
    extraction, _tree = read_agreement(llm, "agreement.txt", text.encode())
    assert extraction["confidence_score"] == 0.9
    assert len(llm.calls) == 2
    assert llm.json_replies == [{"selected_ids": ["c1"]}]


def test_a_blank_field_is_read_from_an_unread_chunk():
    text = "第一条 定义\n术语以本合同为准。\n第二条 订货\n最低订单金额为 USD 500。\n"
    llm = FakeLLM(json_replies=[{"selected_ids": ["c1"]}, dict(_EMPTY), {"selected_ids": ["c2"]}, dict(EXTRACTION)])
    extraction, _tree = read_agreement(llm, "agreement.txt", text.encode())
    assert extraction["minimum_order_quantities"][0]["quantity"] == 500
    assert extraction["territory_text"] == "Sweden."
    reflect = llm.calls[2]["messages"][0]["content"]
    assert "minimum_order_quantities" in reflect and "订货" in reflect and "术语以本合同为准" not in reflect
    assert len(llm.calls) == 4


def test_reflection_stops_after_three_passes():
    text = "第一条 定义\n甲。\n第二条 期限\n自 2026-01-01 起。\n第三条 区域\n瑞典。\n第四条 订货\n最低订单金额为 USD 500。\n"
    llm = FakeLLM(json_replies=[
        {"selected_ids": ["c1"]}, dict(_EMPTY),
        {"selected_ids": ["c2"]}, dict(_EMPTY),
        {"selected_ids": ["c3"]}, dict(_EMPTY),
        {"selected_ids": ["c4"]}, dict(EXTRACTION),
    ])
    with pytest.raises(ValueError, match="No enforceable terms"):
        read_agreement(llm, "agreement.txt", text.encode())
    extracts = [call for call in llm.calls if (call.get("schema") or {}).get("name") == "contract_extraction"]
    assert len(llm.calls) == 6 and len(extracts) == 3
    assert all("最低订单金额" not in call["messages"][0]["content"] for call in extracts)
    assert len(llm.json_replies) == 2


def test_outline_shows_a_heading_sentence_when_there_is_no_short_title():
    text = '1.1 "Container" means one shipping container.\n第二条 订货\n最低订单金额为 USD 500。\n'
    llm = FakeLLM(json_replies=[{"selected_ids": ["c2"]}, dict(EXTRACTION), {"selected_ids": []}])
    read_agreement(llm, "agreement.txt", text.encode())
    outline = llm.calls[0]["messages"][0]["content"]
    assert "Container" in outline and "订货" in outline and "USD 500" not in outline


def test_a_file_with_no_terms_is_not_written(db):
    llm = FakeLLM(json_replies=[{"commercial_discount_rules": [], "minimum_order_quantities": [], "allowed_countries": [], "excluded_countries": [], "field_evidence": {}}])
    with pytest.raises(ValueError, match="No enforceable terms"):
        read_agreement(llm, "empty.txt", b"Hello, this message has no commercial terms at all, just a greeting.")
    assert db.active_contract("ps-1") is None


def _plan(name: str) -> dict:
    return {"tasks": [{"id": "1", "kind": "agent", "name": name, "input": {}, "depends_on": []}]}


def test_chat_files_the_attached_agreement_and_ignores_a_bare_name(db):
    sid = service.new_session(db, "ps-1")["id"]
    named = FakeLLM(
        json_replies=[{"tags": ["contract"], "title": "agreement"}, _plan("contract")],
        text_reply="Please attach the agreement file.",
    )
    ev = list(service.handle_message(db, named, sid, "see the attached agreement", attachments=["agreement.pdf"]))
    assert ev[1]["tasks"][0]["name"] == "contract"
    assert "attach" in "".join(e.get("text", "") for e in ev).lower()
    assert db.active_contract("ps-1") is None
    assert not any((c.get("schema") or {}).get("name") == "contract_extraction" for c in named.calls)

    llm = FakeLLM(json_replies=[_plan("contract"), dict(EXTRACTION)], text_reply="Filed the agreement.")
    ev = list(service.handle_message(db, llm, sid, "please file this", documents=[("agreement.txt", AGREEMENT.encode())]))
    done = ev[-1]
    assert done["contract_id"]
    table = done["contract"]
    assert table["file_name"] == "agreement.txt" and table["term_start_date"] == "2026-01-01"
    assert table["discount_rules"][0]["tiers"][0]["discount_percent"] == 3
    assert db.active_contract("ps-1").term_start_date == "2026-01-01"
    told = llm.calls[-1]["messages"][-1]["content"]
    assert "CONTRACT FILING" in told and "agreement.txt" in told and "2026-01-01" in told
