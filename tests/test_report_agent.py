"""Chat report draft: questions and web lookups are slots; chart points come from rows."""

from __future__ import annotations

from partnerdesk.llm import FakeLLM
from partnerdesk.models import PurchaseOrder
from partnerdesk.prompts.report import STANDARD_QUESTIONS, WITH_DATA
from partnerdesk.report import draft_report
from partnerdesk.report.charts import bind_charts
from partnerdesk.report.turn import parse_split


def _po() -> PurchaseOrder:
    return PurchaseOrder(
        id="po-1", po_number="PO-1", brand_id="brand-1", distributor_id="dist-1", partnership_id="ps-1",
        status="submitted", currency="USD", payment_terms="net_30", incoterms="FOB", shipping_method="sea",
        eta_date=None, ship_to_country="US", notes=None, discount_amount=None, discount_percentage=None,
        subtotal_amount=1000, total_amount=1000,
    )


def test_parse_split_caps_dedupes_and_fills_a_bare_request():
    from datetime import date

    questions, searches = parse_split({
        "questions": [" A ", "a", "b", "c", "d", "e", 3],
        "searches": [" Black Friday ", "black friday", "618", ""],
    }, today=date(2026, 10, 19))
    assert questions == [("q1", "A"), ("q2", "b"), ("q3", "c"), ("q4", "d")]
    assert searches == ["Black Friday", "618"]
    bare_questions, bare_searches = parse_split({}, today=date(2026, 10, 19))
    assert [question for _, question in bare_questions] == list(STANDARD_QUESTIONS)
    assert bare_searches == ["2026年10月 零售档期 促销热点"]


def test_bind_charts_reads_rows_and_drops_specs_that_do_not_match():
    series = {"q1": [{"channel": "Tmall", "units": 10}, {"channel": "JD", "units": 4}]}
    charts = bind_charts([
        {"query_id": "q1", "chart_type": "bar", "x": "channel", "y": "units", "title": "Units"},
        {"query_id": "q1", "chart_type": "bar", "x": "channel", "y": "revenue", "title": "Nope"},
        {"query_id": "q9", "chart_type": "line", "x": "channel", "y": "units", "title": "Missing"},
        {"query_id": "q1", "chart_type": "pie", "x": "channel", "y": "units", "title": "Pie"},
        {"query_id": "q2", "chart_type": "bar", "x": "name", "y": "flag", "title": "Flag"},
        "nope",
    ], {**series, "q2": [{"name": "a", "flag": True}]})
    assert charts == [{
        "title": "Units", "chart_type": "bar",
        "points": [{"x": "Tmall", "y": 10}, {"x": "JD", "y": 4}],
    }]


def test_bind_charts_caps_points_and_keeps_three():
    rows = [{"sku": str(i), "units": i + 1} for i in range(20)]
    series = {f"q{i}": rows for i in range(1, 5)}
    specs = [
        {"query_id": f"q{i}", "chart_type": "line", "x": "sku", "y": "units", "title": f"C{i}"}
        for i in range(1, 5)
    ]
    charts = bind_charts(specs, series)
    assert [chart["title"] for chart in charts] == ["C1", "C2", "C3"]
    assert len(charts[0]["points"]) == 12
    assert charts[0]["points"][0] == {"x": "0", "y": 1}


def test_draft_quotes_rows_and_treats_web_as_context(db):
    db.insert_po(_po(), [], [])

    def lookup(query: str, **_kwargs: object) -> list[dict[str, str]]:
        assert query == "black friday 2026"
        return [{"title": "Black Friday", "snippet": "Retailers plan promotions.", "url": "https://example.com/bf"}]

    llm = FakeLLM(json_replies=[
        {"questions": ["order total"], "searches": ["black friday 2026"]},
        {"tables": ["purchase_orders"]},
        {"sql": "SELECT total_amount FROM purchase_orders"},
        {"charts": [{
            "query_id": "q1", "chart_type": "bar", "x": "total_amount", "y": "total_amount", "title": "Order total",
        }]},
    ], text_reply="Draft says 1000.")
    draft = draft_report(
        db, llm, message="write a short sales note", history=[],
        partnership_id="ps-1", brand_id="brand-1", distributor_id="dist-1", session_id="sess-1",
        search_fn=lookup,
    )
    text_call = next(call for call in llm.calls if call["kind"] == "text")
    body = text_call["messages"][-1]["content"]
    assert text_call["system"] == WITH_DATA
    assert "1000" in body and "Retailers plan promotions." in body and "SELECT" not in body
    assert draft.web_notes == ["Black Friday"]
    assert draft.charts[0]["points"][0]["y"] == 1000
    assert draft.warnings == []


def test_a_failed_lookup_still_returns_a_draft(db):
    def boom(_query: str, **_kwargs: object) -> list[dict[str, str]]:
        raise RuntimeError("BOCHA_API_KEY is not set")

    llm = FakeLLM(json_replies=[
        {"questions": ["weather"], "searches": ["halloween"]},
        {"tables": []},
    ], text_reply="Numbers are missing.")
    draft = draft_report(
        db, llm, message="what about halloween sales", history=[],
        partnership_id="ps-1", brand_id="brand-1", distributor_id="dist-1", session_id="sess-2",
        search_fn=boom,
    )
    assert draft.text == "Numbers are missing."
    assert draft.charts == []
    assert any("No figures" in warning for warning in draft.warnings)
    assert any("BOCHA_API_KEY" in warning for warning in draft.warnings)
