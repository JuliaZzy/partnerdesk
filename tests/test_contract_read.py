"""An agreement file is read into the extraction the contract tables already store.
The model fills the form; a quote that is not in the text is dropped; filing writes
the contract. A chat attachment that is only a name does not pretend to have the file.
"""

from __future__ import annotations

import pytest

from partnerdesk import service
from partnerdesk.contract_read import read_agreement, read_and_ingest, sanitize_extraction
from partnerdesk.llm import FakeLLM

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


def _route():
    return {"topics": [{"topic": "contract_terms", "confidence": 0.91}], "requests": [], "summary": "agreement"}


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
    assert "Pages 1–1" in llm.calls[0]["messages"][0]["content"]


def test_a_file_with_no_terms_is_not_written(db):
    llm = FakeLLM(json_replies=[{"commercial_discount_rules": [], "minimum_order_quantities": [], "allowed_countries": [], "excluded_countries": [], "field_evidence": {}}])
    with pytest.raises(ValueError, match="No enforceable terms"):
        read_agreement(llm, "empty.txt", b"Hello, this message has no commercial terms at all, just a greeting.")
    assert db.active_contract("ps-1") is None


def test_chat_files_the_attached_agreement_and_ignores_a_bare_name(db):
    sid = service.new_session(db, "ps-1")["id"]
    named = FakeLLM(json_replies=[_route()])
    ev = list(service.handle_message(db, named, sid, "see the attached agreement", attachments=["agreement.pdf"]))
    assert ev[0]["specialist"] == "contract" and "Attach the agreement" in ev[1]["text"]
    assert db.active_contract("ps-1") is None
    assert len([c for c in named.calls if c["kind"] == "json"]) == 1

    llm = FakeLLM(json_replies=[_route(), dict(EXTRACTION)])
    ev = list(service.handle_message(db, llm, sid, "please file this", documents=[("agreement.txt", AGREEMENT.encode())]))
    assert ev[0]["specialist"] == "contract" and ev[-1]["contract_id"]
    assert "Filed agreement.txt" in ev[1]["text"]
    assert db.active_contract("ps-1").term_start_date == "2026-01-01"
