"""The desk's HTTP surface: the pages the web client renders, against the same database."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.contract import ingest_extraction
from backend.llm import FakeLLM
from backend.models import Partnership, Product


def client(db, llm=None) -> TestClient:
    return TestClient(create_app(db=db, llm=llm or FakeLLM()))


def test_meta_lists_the_campus_models(db):
    body = client(db).get("/api/meta").json()
    assert "qwen" in [m["id"] for m in body["models"]]
    assert body["default_model"]
    assert "停产" in body["product_statuses"]
    assert body["memory_kinds"]["preference"] == "Preference"


def test_catalog_filters_and_needs_no_partnership(db, make_db):
    c = client(db)
    listed = c.get("/api/catalog").json()
    assert {x["sku"] for x in listed["items"]} == {"FO-100", "SR-200", "SR-201", "NP-300", "GS-010", "GU-020", "OLD-900"}
    assert {x["brand_name"] for x in listed["items"]} == {"Brand"}

    native = c.get("/api/catalog", params={"q": "鱼油"}).json()
    assert [x["sku"] for x in native["items"]] == ["FO-100"]

    retired = c.get("/api/catalog", params={"status": "停产"}).json()
    assert [x["sku"] for x in retired["items"]] == ["OLD-900"]

    bare = make_db("bare")
    bare.upsert_brand("b-solo", "Solo Foods")
    bare.upsert_product("b-solo", Product(id="p-1", sku="SF-1", product_name_en="Oat Drink", product_name_native="Haferdrink"))
    solo = client(bare)
    assert solo.get("/api/partnerships").json() == []
    assert solo.get("/api/catalog").json()["items"][0]["product_name_native"] == "Haferdrink"


def test_sessions_list_is_newest_first(db):
    c = client(db)
    first = c.post("/api/sessions", json={"partnership_id": "ps-1"}).json()["id"]
    second = c.post("/api/sessions", json={"partnership_id": "ps-1"}).json()["id"]
    rows = c.get("/api/sessions", params={"partnership_id": "ps-1"}).json()
    assert [r["id"] for r in rows[:2]] == [second, first]
    assert "history" not in rows[0]


def test_orders_include_the_submitted_day(db):
    from datetime import timedelta

    from backend.po.check import run_code_check
    from backend.po.slots import Discount, LineItemSlot, PoSlots
    from backend.po.submit import create_and_submit_po
    from backend.po.turn import load_bundle

    bundle = load_bundle(db, "ps-1")
    slots = PoSlots(line_items=[LineItemSlot(reference="FO-100", cases=20)], discount=Discount(kind="none"))
    check = run_code_check(slots=slots, catalog=bundle.catalog, rules=bundle.rules, partnership_id="ps-1", ship_to_country="US")
    result = create_and_submit_po(db, partnership=bundle.partnership, settings=bundle.settings, slots=slots, check=check)
    submitted_at = db.po(result.po_id).submitted_at
    assert submitted_at.tzinfo is not None and submitted_at.utcoffset() == timedelta(0)

    body = client(db).get("/api/orders", params={"partnership_id": "ps-1"}).json()
    row = next(o for o in body if o["po_number"] == result.po_number)
    assert row["submitted_at"].startswith(submitted_at.date().isoformat())
    assert row["items"][0]["sku"] == "FO-100"


def test_contract_detail_shows_the_next_discount_and_derived_rules(db, partnership: Partnership):
    rec = ingest_extraction(
        db,
        {
            "minimum_order_quantities": [{"quantity": 500, "unit": "USD", "applies_per": "per_order"}],
            "commercial_discount_rules": [{
                "title": "Container sequence discount",
                "tiers": [{"from_container": 1, "to_container": 2, "discount_percent": 3}],
            }],
        },
        brand_id=partnership.brand_id, partnership_id=partnership.id, title="Distribution 2026",
    )
    body = client(db).get(f"/api/contracts/{rec.contract.id}").json()
    assert body["contract"]["title"] == "Distribution 2026"
    assert body["next_discount"]["percent"] == 3
    assert len(body["terms"]["discount_rules"]) == 1
    assert any(r["rule_type"] == "min_order_value" for r in body["rules"])


def test_memory_round_trip(db):
    c = client(db)
    assert c.get("/api/partnerships/ps-1/memories").json()["active"] == []
    saved = c.post("/api/partnerships/ps-1/memories", json={"content": "Prefers air freight for launches", "kind": "preference"}).json()
    assert saved["content"] == "Prefers air freight for launches" and saved["source"] == "user"
    active = c.get("/api/partnerships/ps-1/memories").json()
    assert "Prefers air freight for launches" in active["block"]
    assert c.patch(f"/api/memories/{saved['id']}", json={"active": False}).status_code == 200
    after = c.get("/api/partnerships/ps-1/memories").json()
    assert after["active"] == [] and after["inactive"][0]["is_active"] is False
    assert c.post("/api/partnerships/ps-1/memories", json={"content": "  ", "kind": "fact"}).status_code == 400


def test_report_confirm_and_delete(db, partnership: Partnership):
    c = client(db)
    csv = b"SKU,Units sold,Sales (USD)\nFO-100,120,1200\nSR-200,30,1500\n"
    preview = c.post(
        "/api/partnerships/ps-1/reports/preview",
        files={"file": ("aug.csv", csv, "text/csv")},
        data={"period": "2025-08"},
    ).json()
    assert preview["fact_count"] == 4

    saved = c.post(
        "/api/partnerships/ps-1/reports",
        files={"file": ("aug.csv", csv, "text/csv")},
        data={"period": "2025-08", "title": "August"},
    ).json()
    assert saved["title"] == "August" and saved["status"] == "draft"
    out = c.post(f"/api/reports/{saved['id']}/confirm").json()
    assert out["confirmed"] == 4
    facts = c.get("/api/partnerships/ps-1/facts").json()
    assert len(facts) == 4
    assert c.delete(f"/api/reports/{saved['id']}").status_code == 204
    assert db.report(saved["id"]) is None
