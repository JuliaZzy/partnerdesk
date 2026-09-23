"""Brand knowledge: the two passes are scripted, their output is normalized and stored as
draft, approval is a person's, and a re-run never loses what was approved."""

from __future__ import annotations

import pytest

from partnerdesk import knowledge
from partnerdesk.knowledge import (
    add_fragment,
    distill,
    distill_document,
    ingest_document,
    load_pages,
    match_product,
)
from partnerdesk.llm import FakeLLM
from partnerdesk.models import Product

SCAN = {
    "summary": "A product deck for Deep Sea Fish Oil with a page of brand history.",
    "types": [
        {"type": "Product", "label": "Products", "description": "Product pages.", "page_numbers": [1, 2]},
        {"type": "brand_story", "label": "Brand story", "description": "Founding and mission.", "page_numbers": [3]},
    ],
}
EXTRACT = {
    "fragments": [
        {"type": "product", "title": "Deep Sea Fish Oil", "content": "1000 mg omega-3 per softgel; IFOS 5-star certified; 90 softgels per bottle.", "product_name": "Deep Sea Fish Oil", "source_pages": [1, 2], "tags": ["omega-3", "IFOS"]},
        {"type": "Brand Story", "title": "Founded in Bergen", "content": "Founded in Bergen in 2009 by two marine biologists.", "product_name": None, "source_pages": [3], "tags": []},
        {"type": "product", "title": "Empty one", "content": "   ", "product_name": None, "source_pages": [], "tags": []},
    ]
}
PAGES = [(1, "Deep Sea Fish Oil — 1000 mg omega-3 per softgel."), (2, "IFOS 5-star certified. 90 softgels."), (3, "Founded in Bergen in 2009.")]
PRODUCTS = [Product(id="p-fo", sku="FO-100", product_name_en="Deep Sea Fish Oil"), Product(id="p-sr", sku="SR-200", product_name_en="Radiance Serum")]


def test_load_pages_chunks_plain_text_and_honours_form_feeds():
    assert load_pages("notes.txt", b"page one\fpage two") == [(1, "page one"), (2, "page two")]
    (single,) = load_pages("brand.md", b"# Aurora\n\nBotanicals from the north.")
    assert single == (1, "# Aurora\n\nBotanicals from the north.")
    long = ("para " * 400 + "\n\n") * 6
    assert len(load_pages("long.txt", long.encode())) > 1
    with pytest.raises(ValueError, match="Unsupported"):
        load_pages("deck.pptx", b"")


def test_distill_normalizes_and_links_products():
    llm = FakeLLM(json_replies=[dict(SCAN), dict(EXTRACT)])
    d = distill(llm, brand_id="brand-1", file_name="deck.pdf", pages=PAGES, products=PRODUCTS)
    assert d.summary.startswith("A product deck") and d.pages_processed == 3 and d.skipped == []
    assert [(s.type, s.page_numbers) for s in d.sections] == [("product", [1, 2]), ("brand_story", [3])]
    assert [f.type for f in d.fragments] == ["product", "brand_story"]  # slugged; the empty one dropped
    fish, story = d.fragments
    assert fish.product_id == "p-fo" and fish.source_pages == [1, 2] and fish.tags == ["omega-3", "IFOS"] and fish.status == "draft"
    assert story.product_id is None and story.product_name is None
    # Both passes saw the pages; the extract pass was told what the scan found.
    scan_call, extract_call = llm.calls
    assert "Pages 1–3" in scan_call["messages"][0]["content"] and "--- Page 2 ---" in scan_call["messages"][0]["content"]
    assert "brand_story (Brand story)" in extract_call["messages"][0]["content"]


def test_distill_batches_long_documents_and_reports_blank_pages():
    pages = [(i, f"page {i} text") for i in range(1, 6)] + [(6, "")]
    scan = {"summary": "s", "types": [{"type": "product", "label": "P", "description": "", "page_numbers": [1]}]}
    llm = FakeLLM(json_replies=[dict(scan), {"fragments": []}, dict(scan), {"fragments": []}, dict(scan), {"fragments": []}])
    d = distill(llm, brand_id="brand-1", file_name="cat.pdf", pages=pages, pages_per_batch=2)
    assert len(llm.calls) == 6 and d.pages_processed == 5 and d.skipped == ["page 6: no text layer (scanned page — needs a vision model)"]
    with pytest.raises(ValueError, match="No readable text"):
        distill(FakeLLM(), brand_id="brand-1", file_name="scan.pdf", pages=[(1, ""), (2, "  ")])


def test_match_product_by_name_or_sku():
    assert match_product(PRODUCTS, "deep sea fish oil").id == "p-fo"
    assert match_product(PRODUCTS, None, "SR-200").id == "p-sr"
    assert match_product(PRODUCTS, "Night Cream") is None and match_product(PRODUCTS, None) is None
    # What the model actually writes: the name with the SKU in brackets, or a longer variant name.
    assert match_product(PRODUCTS, "Radiance Serum (SR-200)").id == "p-sr"
    plus = [*PRODUCTS, Product(id="p-srp", sku="SR-201", product_name_en="Radiance Serum Plus")]
    assert match_product(plus, "Radiance Serum Plus (SR-201)").id == "p-srp"
    assert match_product(plus, "The Radiance Serum Plus range").id == "p-srp"
    assert match_product(plus, "Serums") is None


def test_document_lifecycle_in_the_database(db):
    doc = ingest_document(db, brand_id="brand-1", file_name="deck.txt", data=b"Deep Sea Fish Oil\f1000 mg omega-3\fFounded in Bergen", title="Spring deck")
    assert doc.status == "uploaded" and doc.page_count == 3 and db.document_pages(doc.id)[2] == (3, "Founded in Bergen")
    assert [d.id for d in db.documents("brand-1")] == [doc.id]

    llm = FakeLLM(json_replies=[dict(SCAN), dict(EXTRACT)])
    result = distill_document(db, llm, doc.id, products=db.products("brand-1"))
    stored = db.document(doc.id)
    assert stored.status == "distilled" and stored.summary == SCAN["summary"] and stored.pages_processed == 3 and stored.distilled_at
    assert [s.type for s in db.document_sections(doc.id)] == ["product", "brand_story"]
    drafts = db.fragments("brand-1", status="draft")
    assert {f.title for f in drafts} == {"Deep Sea Fish Oil", "Founded in Bergen"} and all(f.document_id == doc.id for f in drafts)
    assert next(f for f in drafts if f.title == "Deep Sea Fish Oil").product_id == "p-fo"

    # A person approves one; a re-run replaces the drafts but keeps the approved fragment.
    fish = next(f for f in drafts if f.title == "Deep Sea Fish Oil")
    db.set_fragment_status(fish.id, "approved")
    rerun = FakeLLM(json_replies=[dict(SCAN), {"fragments": [{"type": "proof", "title": "Sold in 12 countries", "content": "Sold in 12 countries as of 2025.", "product_name": None, "source_pages": [3], "tags": []}]}])
    distill_document(db, rerun, doc.id, products=db.products("brand-1"))
    titles = {f.title: f.status for f in db.fragments("brand-1")}
    assert titles == {"Deep Sea Fish Oil": "approved", "Sold in 12 countries": "draft"}
    assert [f.title for f in db.fragments("brand-1", status="approved")] == ["Deep Sea Fish Oil"]
    assert [f.type for f in db.fragments("brand-1", type="proof")] == ["proof"]
    assert len(result.fragments) == 2

    # Deleting the document keeps the fragments (unlinked) and drops the pages.
    db.delete_document(doc.id)
    assert db.document(doc.id) is None and db.document_pages(doc.id) == []
    assert all(f.document_id is None for f in db.fragments("brand-1"))


def test_failed_distillation_is_recorded(db):
    doc = ingest_document(db, brand_id="brand-1", file_name="deck.txt", data=b"some text")
    with pytest.raises(RuntimeError):
        distill_document(db, FakeLLM(fail_json=True), doc.id)
    assert db.document(doc.id).status == "failed" and db.fragments("brand-1") == []


def test_hand_written_fragment_is_approved_and_linked_by_sku(db):
    f = add_fragment(db, brand_id="brand-1", type="Usage", title="How to take it", content="Two softgels with food.", product_name="FO-100", tags=["dosage"], products=db.products("brand-1"))
    (stored,) = db.fragments("brand-1")
    assert stored.id == f.id and stored.status == "approved" and stored.type == "usage" and stored.product_id == "p-fo" and stored.document_id is None
    db.set_fragment_status(f.id, "archived")
    assert db.fragments("brand-1", status="approved") == []
    db.delete_fragment(f.id)
    assert db.fragments("brand-1") == []
    assert "product" in knowledge.SEED_TYPES
