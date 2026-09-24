"""The desk (ui.py + ui_pages/), driven headless with AppTest: it opens on the chat, every page
renders, and the pages act on the same database the agent reads."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from streamlit.testing.v1 import AppTest

from partnerdesk import reports
from partnerdesk.config import FIXTURES_DIR
from partnerdesk.contracts import ingest_extraction
from partnerdesk.knowledge import add_fragment
from partnerdesk.llm import FakeLLM
from partnerdesk.models import Partnership
from partnerdesk.po.check import run_code_check
from partnerdesk.po.slots import Discount, LineItemSlot, PoSlots
from partnerdesk.po.submit import create_and_submit_po
from partnerdesk.po.turn import load_bundle

DESK = Path(__file__).resolve().parent.parent / "partnerdesk" / "ui.py"
PAGES = ("ui_pages/orders.py", "ui_pages/contracts.py", "ui_pages/reports.py", "ui_pages/knowledge.py", "ui_pages/memory.py")


def desk(db, llm=None) -> AppTest:
    at = AppTest.from_file(str(DESK), default_timeout=60)
    at.session_state["_backend"] = (db, llm or FakeLLM())
    return at


def text_of(at: AppTest) -> str:
    return "\n".join(str(getattr(e, "value", "")) for e in [*at.markdown, *at.caption, *at.header, *at.subheader, *at.info, *at.metric, *at.success, *at.warning])


def test_desk_opens_on_the_chat_and_every_page_renders(db):
    at = desk(db).run()
    assert not at.exception
    assert "What would you like" in text_of(at)  # the chat page's greeting
    for page in PAGES:
        at.switch_page(page).run()
        assert not at.exception, page
    assert [h.value for h in at.header] == ["Memory"]


def test_orders_page_renders_a_placed_order(db):
    """The only page that formats a timestamp the agent wrote rather than a seeded one."""
    bundle = load_bundle(db, "ps-1")
    slots = PoSlots(line_items=[LineItemSlot(reference="FO-100", cases=20)], discount=Discount(kind="none"))
    check = run_code_check(slots=slots, catalog=bundle.catalog, rules=bundle.rules, partnership_id="ps-1", ship_to_country="US")
    result = create_and_submit_po(db, partnership=bundle.partnership, settings=bundle.settings, slots=slots, check=check)
    submitted_at = db.po(result.po_id).submitted_at
    assert submitted_at.tzinfo is not None and submitted_at.utcoffset() == timedelta(0)

    at = desk(db).run().switch_page("ui_pages/orders.py").run()
    assert not at.exception
    row = at.dataframe[0].value.iloc[0]
    assert row["PO"] == result.po_number and row["Submitted"] == submitted_at.date().isoformat()


def test_contracts_page_shows_the_agreement_and_the_next_discount(db, partnership: Partnership):
    fixture = json.loads((FIXTURES_DIR / "contracts.json").read_text(encoding="utf-8"))[0]
    ingest_extraction(db, fixture["extraction"], brand_id=partnership.brand_id, partnership_id=partnership.id, title="Aurora × Nordic 2026")
    at = desk(db).run()
    at.switch_page("ui_pages/contracts.py").run()
    assert not at.exception
    page = text_of(at)
    assert "Aurora × Nordic 2026" in page and "Container sequence discount" in page
    assert [m.value for m in at.metric][:2] == ["3%", "1"]  # first container of the year → 3%, one schedule
    rules_tab = [t for t in at.table if any("min_order_value" in json.dumps(row, default=str) for row in t.value.to_dict("records"))]
    assert rules_tab, "the PO rules derived from the contract are shown"


def test_memory_page_remembers_for_the_agent(db):
    at = desk(db).run()
    at.switch_page("ui_pages/memory.py").run()
    assert not at.exception and "Nothing yet" in text_of(at)
    at.text_input(key="mem_content").set_value("Prefers air freight for launches")
    next(b for b in at.button if b.label == "Remember").click().run()
    assert not at.exception
    (m,) = db.memories("ps-1")
    assert m.content == "Prefers air freight for launches" and m.source == "user"
    assert "Prefers air freight for launches" in text_of(at)
    at.button(key=f"off-{m.id}").click().run()
    assert not at.exception and db.memories("ps-1") == [] and not db.memories("ps-1", active_only=False)[0].is_active


def test_reports_page_confirms_a_draft_and_shows_live_numbers(db, partnership: Partnership):
    csv = b"SKU,Units sold,Sales (USD)\nFO-100,120,1200\nSR-200,30,1500\n"
    rep = reports.ingest(db, partnership, reports.analyze("aug.csv", csv, reporting_period="2025-08", currency="USD", products=db.products("brand-1")), title="August")
    at = desk(db).run()
    at.switch_page("ui_pages/reports.py").run()
    assert not at.exception
    assert "August" in text_of(at) and "Confirm a report to see its numbers" in text_of(at)
    at.button(key=f"confirm-{rep.id}").click().run()
    assert not at.exception
    assert db.report(rep.id).status == "confirmed" and len(db.confirmed_facts(partnership.id)) == 4
    values = [m.value for m in at.metric]
    assert "USD 2,700" in values and "150" in values  # revenue and units from the confirmed facts
    at.button(key=f"delete-{rep.id}").click().run()
    assert not at.exception and db.report(rep.id) is None


def test_knowledge_status_filter_keeps_fragments_tab(db):
    add_fragment(db, brand_id="brand-1", type="product", title="Deep Sea Fish Oil", content="1000 mg omega-3 per softgel.", product_name="FO-100", products=db.products("brand-1"), status="draft")
    at = desk(db).run()
    at.switch_page("ui_pages/knowledge.py").run()
    assert not at.exception
    at.session_state["knowledge-tabs"] = "Fragments"
    at.session_state["knowledge-tabs__active"] = "Fragments"
    at.run()
    assert at.session_state["knowledge-tabs"] == "Fragments"
    at.segmented_control(key="kb_status").select("draft").run()
    assert not at.exception
    assert at.session_state["knowledge-tabs"] == "Fragments"


def test_knowledge_page_lists_fragments_and_approves_them(db):
    f = add_fragment(db, brand_id="brand-1", type="product", title="Deep Sea Fish Oil", content="1000 mg omega-3 per softgel.", product_name="FO-100", products=db.products("brand-1"), status="draft")
    at = desk(db).run()
    at.switch_page("ui_pages/knowledge.py").run()
    assert not at.exception
    page = text_of(at)
    assert "1000 mg omega-3 per softgel." in page and "Deep Sea Fish Oil" in page
    at.button(key=f"approve-{f.id}").click().run()
    assert not at.exception
    assert db.fragments("brand-1")[0].status == "approved"
    assert at.metric[2].value == "1"  # Approved
