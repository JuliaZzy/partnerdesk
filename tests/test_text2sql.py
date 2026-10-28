"""Text-to-SQL: the statement is checked, then run only against this partnership's views."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend import service
from backend.app import create_app
from backend.db.orm import Base
from backend.llm import FakeLLM
from backend.schemas import Partnership, Product, PurchaseOrder
from backend.text2sql import answer_question
from backend.text2sql.analyze_badcase import analyze, classify_error
from backend.text2sql.prompt import ANSWER as DATA_ANSWER
from backend.text2sql.prompt import REFLECT
from backend.text2sql.sql_validator import validate
from backend.text2sql.table_metadata import EXCLUDED_TABLES, HIDDEN_COLUMNS, TABLES, scope_views


def _models(monkeypatch, replies: list[dict]):
    """Script the campus and DeepSeek clients. The queue is shared, in call order."""
    pending = list(replies)
    school: list[dict] = []

    class _School:
        def chat_completion(self, model, messages, stream=False, max_tokens=None, temperature=None):
            school.append({"model": model, "messages": messages})
            raw = pending.pop(0)
            return {"choices": [{"message": {"content": json.dumps(raw)}}]}

    class _DeepSeek:
        def response(self, **_kwargs):
            raw = pending.pop(0)
            return type("Reply", (), {"output_text": json.dumps(raw)})()

    monkeypatch.setattr("backend.text2sql.text2sql.SJTUClient", lambda settings: _School())
    monkeypatch.setattr("backend.text2sql.text2sql.DeepSeekClient", lambda settings: _DeepSeek())
    return pending, school


def _po(**over) -> PurchaseOrder:
    base = dict(
        id="po-1", po_number="PO-1", brand_id="brand-1", distributor_id="dist-1", partnership_id="ps-1",
        status="submitted", currency="USD", payment_terms="net_30", incoterms="FOB", shipping_method="sea",
        eta_date=None, ship_to_country="US", notes=None, discount_amount=None, discount_percentage=None,
        subtotal_amount=1000, total_amount=1000,
    )
    return PurchaseOrder(**{**base, **over})


def test_metadata_matches_the_tables_and_hides_bytes_and_sessions():
    names = {table.name for table in Base.metadata.sorted_tables}
    assert set(TABLES) | EXCLUDED_TABLES == names
    assert "sessions" in EXCLUDED_TABLES and "text2sql_logs" in EXCLUDED_TABLES
    for name, meta in TABLES.items():
        columns = {column.name for column in Base.metadata.tables[name].columns}
        assert set(meta["columns"]) == columns - HIDDEN_COLUMNS.get(name, frozenset())
    views = dict(scope_views())
    assert "file_bytes" not in views["brand_documents"]
    assert "sessions" not in views


def test_validator_accepts_a_read_and_caps_the_limit():
    ok, err, sql = validate("SELECT term_end_date FROM contracts WHERE term_end_date >= '2026-01-01'")
    assert ok and err == "" and "term_end_date" in sql and "LIMIT 100" in sql.upper()

    ok, _, sql = validate("SELECT id FROM products LIMIT 99999")
    assert ok and "LIMIT 200" in sql.upper()

    ok, _, sql = validate("SELECT product_name_en FROM products WHERE product_name_en ILIKE '%drop table%'")
    assert ok and "drop table" in sql.lower()


@pytest.mark.parametrize("sql, message", [
    ("", "empty"),
    ("DELETE FROM purchase_orders", "only SELECT"),
    ("INSERT INTO purchase_orders (id) VALUES ('x')", "only SELECT"),
    ("UPDATE purchase_orders SET notes = 'x'", "only SELECT"),
    ("DROP TABLE purchase_orders", "only SELECT"),
    ("SELECT id FROM purchase_orders; DELETE FROM purchase_orders", "multiple"),
    ("SELECT pg_read_file('/etc/passwd')", "function"),
    ("SELECT id FROM public.purchase_orders", "schema-qualified"),
    ("SELECT confirm_token FROM sessions", "not allowed"),
    ("SELECT set_config('search_path', 'public', true)", "function"),
])
def test_validator_rejects_writes_and_escapes(sql, message):
    ok, err, cleaned = validate(sql)
    assert not ok and cleaned == "" and message in err


def test_validator_allows_a_qualified_column_inside_and_and_or():
    joined = (
        "SELECT coalesce(sum(rf.numeric_value), 0) FROM report_facts rf "
        "JOIN sales_reports sr ON rf.report_id = sr.id "
        "WHERE rf.status = 'confirmed' AND rf.metric_key = 'units_sold' "
        "AND sr.status = 'confirmed' AND sr.reporting_period = ("
        "SELECT max(reporting_period) FROM sales_reports WHERE status = 'confirmed')"
    )
    ok, err, sql = validate(joined)
    assert ok and err == "" and "numeric_value" in sql.lower()

    either = (
        "SELECT id FROM report_facts rf JOIN sales_reports sr ON rf.report_id = sr.id "
        "WHERE rf.status = 'confirmed' OR sr.status = 'confirmed'"
    )
    ok, err, sql = validate(either)
    assert ok and err == "" and " or " in sql.lower()


def test_validator_accepts_a_bare_function_and_rejects_a_schema_prefix():
    ok, err, sql = validate("SELECT date_trunc('month', created_at) FROM purchase_orders")
    assert ok and err == "" and "date_trunc" in sql.lower()

    for qualified in (
        "SELECT pg_catalog.date_trunc('month', created_at) FROM purchase_orders",
        "SELECT public.max(total_amount) FROM purchase_orders",
        'SELECT "pg_catalog.date_trunc"(\'month\', created_at) FROM purchase_orders',
    ):
        ok, err, cleaned = validate(qualified)
        assert not ok and cleaned == "" and "schema-qualified" in err


@pytest.mark.parametrize("sql", [
    "SELECT pg_catalog.pg_sleep(1)",
    "SELECT public.pg_read_file('/etc/passwd')",
    'SELECT "pg_catalog.pg_sleep"(1)',
])
def test_validator_rejects_a_forbidden_function_with_a_schema(sql):
    ok, err, cleaned = validate(sql)
    assert not ok and cleaned == "" and "function" in err


def test_a_rejected_statement_is_kept_for_the_repair(db, monkeypatch):
    _models(monkeypatch, [
        {"tables": ["purchase_orders"]},
        {"sql": "SELECT pg_read_file('/etc/passwd')"},
        {"sql": "SELECT pg_sleep(1)"},
    ])
    result = answer_question(
        db,
        question="read a file", partnership_id="ps-1", brand_id="brand-1", distributor_id="dist-1",
    )
    assert result.ok is False and result.error_type == "validation"
    logged = db.list_text2sql_logs("ps-1")[0]["generated_sql"]
    assert logged and "pg_sleep" in logged


def test_a_schema_qualified_function_is_rejected_then_repaired(db, monkeypatch):
    db.insert_po(_po(), [], [])
    _models(monkeypatch, [
        {"tables": ["purchase_orders"]},
        {"sql": "SELECT pg_catalog.date_trunc('month', submitted_at) AS month FROM purchase_orders"},
        {"sql": "SELECT date_trunc('month', submitted_at) AS month FROM purchase_orders"},
    ])
    result = answer_question(
        db,
        question="orders by month", partnership_id="ps-1", brand_id="brand-1", distributor_id="dist-1",
    )
    assert result.ok, result.error
    assert "pg_catalog" not in (result.sql or "").lower()
    assert "date_trunc" in (result.sql or "").lower()


def test_a_question_only_sees_this_partnership(db, monkeypatch):
    db.upsert_partnership(Partnership(
        id="ps-2", brand_id="brand-2", distributor_id="dist-2", brand_name="Other", distributor_name="Other",
        ship_to_country="DE", currency="EUR",
    ))
    db.upsert_product("brand-2", Product(id="p-secret", sku="XX-1", product_name_en="Secret Sauce"))
    db.insert_po(_po(), [], [])
    db.insert_po(_po(
        id="po-2", po_number="PO-2", brand_id="brand-2", distributor_id="dist-2", partnership_id="ps-2",
        total_amount=9999, subtotal_amount=9999, currency="EUR", ship_to_country="DE",
    ), [], [])

    _models(monkeypatch, [
        {"tables": ["purchase_orders"]},
        {"sql": "SELECT total_amount FROM purchase_orders"},
    ])
    orders = answer_question(
        db,
        question="what did I order?", partnership_id="ps-1", brand_id="brand-1", distributor_id="dist-1",
    )
    assert orders.ok
    assert {row["total_amount"] for row in orders.rows} == {1000}

    _models(monkeypatch, [
        {"tables": ["products"]},
        {"sql": "SELECT sku FROM products WHERE product_name_en ILIKE '%fish%'"},
    ])
    fish = answer_question(
        db,
        question="fish oil sku", partnership_id="ps-1", brand_id="brand-1", distributor_id="dist-1",
    )
    assert fish.ok and [row["sku"] for row in fish.rows] == ["FO-100"]

    _models(monkeypatch, [
        {"tables": ["products"]},
        {"sql": "SELECT sku FROM products"},
    ])
    whole = answer_question(
        db,
        question="list skus", partnership_id="ps-1", brand_id="brand-1", distributor_id="dist-1",
    )
    assert "XX-1" not in {row["sku"] for row in whole.rows}


def test_a_write_is_refused_and_the_order_stays(db, monkeypatch):
    db.insert_po(_po(), [], [])
    _models(monkeypatch, [
        {"tables": ["purchase_orders"]},
        {"sql": "DELETE FROM purchase_orders"},
        {"sql": "DELETE FROM purchase_orders"},
    ])
    result = answer_question(
        db,
        question="delete my orders", partnership_id="ps-1", brand_id="brand-1", distributor_id="dist-1",
        session_id=db.create_session("sess-1", "ps-1")["id"],
    )
    assert result.ok is False and result.error_type == "validation"
    assert db.po("po-1") is not None
    assert db.list_text2sql_logs("ps-1")[0]["execution_success"] is False


def test_a_bad_column_is_repaired_once(db, monkeypatch):
    db.insert_po(_po(), [], [])
    _models(monkeypatch, [
        {"tables": ["purchase_orders"]},
        {"sql": "SELECT missing_column FROM purchase_orders"},
        {"sql": "SELECT total_amount FROM purchase_orders"},
    ])
    result = answer_question(
        db,
        question="order total", partnership_id="ps-1", brand_id="brand-1", distributor_id="dist-1",
    )
    assert result.ok and result.rows == [{"total_amount": 1000}]
    assert "missing_column" in result.sql or "total_amount" in (result.sql or "")


def test_chat_quotes_the_rows_and_a_report_uses_them(db, monkeypatch):
    db.insert_po(_po(), [], [])
    sid = db.create_session("sess-data", "ps-1")["id"]
    _models(monkeypatch, [
        {"tables": ["purchase_orders"]},
        {"sql": "SELECT total_amount FROM purchase_orders"},
    ])
    llm = FakeLLM(json_replies=[
        {"tags": ["data"], "title": "orders"},
        {"tasks": [{"id": "1", "kind": "agent", "name": "data", "input": {}, "depends_on": []}]},
    ], text_reply="The order total is 1000.")
    events = list(service.handle_message(db, llm, sid, "how much have I ordered?"))
    assert any(event.get("type") == "data" and event["ok"] and event["row_count"] == 1 for event in events)
    text_call = next(call for call in llm.calls if call["kind"] == "text")
    assert text_call["system"] == DATA_ANSWER
    body = text_call["messages"][-1]["content"]
    done = events[-1]
    assert done["data_query"]["log_id"]
    assert "1000" in body and "9999" not in body

    sid = db.create_session("sess-report", "ps-1")["id"]
    report_llm = FakeLLM(json_replies=[
        {"tags": ["report", "data"], "title": "report"},
        {"tasks": [
            {"id": "d", "kind": "agent", "name": "data", "input": {"question": "order total"}, "depends_on": []},
            {"id": "r", "kind": "agent", "name": "report", "input": {}, "depends_on": ["d"]},
        ]},
        {"title": "订单月报", "sections": [
            {"id": "metrics", "questions": ["order total"], "searches": []},
            {"id": "summary", "questions": [], "searches": []},
            {"id": "performance", "questions": ["order total"], "searches": []},
            {"id": "inventory", "questions": ["order total"], "searches": []},
            {"id": "next", "questions": [], "searches": []},
            {"id": "risks", "questions": ["should not run yet"], "searches": ["should not search yet"]},
        ]},
        {"charts": [{
            "id": "chart_001",
            "title": "Order total",
            "subtitle": "",
            "type": "bar",
            "echarts_option": {"series": [{"type": "bar", "data": [1000]}]},
        }]},
        {"charts": [{
            "id": "chart_001",
            "title": "Order total",
            "subtitle": "",
            "type": "bar",
            "echarts_option": {"series": [{"type": "bar", "data": [1000]}]},
        }]},
        {"code": "rows = datasets[0]['rows']\ninsights = [{'title': '订单总额', 'detail': '总额 ' + str(rows[0]['total_amount']), 'evidence': {'total': rows[0]['total_amount']}}]"},
        {"code": "rows = datasets[0]['rows']\ninsights = [{'title': '订单总额', 'detail': '总额 ' + str(rows[0]['total_amount']), 'evidence': {'total': rows[0]['total_amount']}}]"},
        {"code": "rows = datasets[0]['rows']\ninsights = [{'title': '订单总额', 'detail': '总额 ' + str(rows[0]['total_amount']), 'evidence': {'total': rows[0]['total_amount']}}]"},
        {"code": "rows = datasets[0]['rows']\ninsights = [{'title': '订单总额', 'detail': '总额 ' + str(rows[0]['total_amount']), 'evidence': {'total': rows[0]['total_amount']}}]"},
        {"content": "Report: 1000.", "key_points": ["1000"]},
        {"content": "Report: 1000.", "key_points": ["1000"]},
        {"content": "Report: 1000.", "key_points": ["1000"]},
        {"content": "Report: 1000.", "key_points": ["1000"]},
        {"write": True, "questions": ["open purchase orders"], "searches": []},
        {"code": "rows = datasets[0]['rows']\ninsights = [{'title': '订单总额', 'detail': '总额 ' + str(rows[0]['total_amount']), 'evidence': {'total': rows[0]['total_amount']}}]"},
        {"content": "未结订单还在。", "key_points": ["未结"]},
        {"content": "Report: 1000.", "key_points": ["1000"]},
        {"score": 90, "ok": True, "issues": []},
    ], text_reply="Report: 1000.")
    _pending, query_school = _models(monkeypatch, [
        {"tables": ["purchase_orders"]},
        {"sql": "SELECT total_amount FROM purchase_orders"},
        {"score": 90, "retry_question": ""},
        {"tables": ["purchase_orders"]},
        {"sql": "SELECT total_amount FROM purchase_orders"},
        {"score": 90, "retry_question": ""},
    ])
    events = list(service.handle_message(db, report_llm, sid, "write a short sales note"))
    reflects = [call for call in query_school if call["messages"][0]["content"] == REFLECT]
    assert len(reflects) == 2
    assert "order total" in reflects[0]["messages"][1]["content"]
    assert "open purchase orders" in reflects[1]["messages"][1]["content"]
    write_calls = [call for call in report_llm.calls if call["kind"] == "json" and "营销报告撰写人" in call["system"]]
    wizard_calls = [call for call in report_llm.calls if call["kind"] == "json" and "text2sql 的全部输出" in call["system"]]
    assert len(wizard_calls) == 5
    assert "关键指标" in wizard_calls[0]["system"] and "1000" in wizard_calls[0]["system"]
    assert "order total" in wizard_calls[0]["system"]
    assert "问题与风险" in wizard_calls[-1]["system"]
    assert all("问题与风险" not in call["system"] for call in wizard_calls[:-1])
    risk_at = next(i for i, call in enumerate(report_llm.calls) if "要不要写" in call["system"])
    assert "Report: 1000." in report_llm.calls[risk_at]["messages"][-1]["content"]
    assert "should not run yet" not in report_llm.calls[risk_at]["messages"][-1]["content"]
    later = [
        i for i, call in enumerate(report_llm.calls)
        if "open purchase orders" in call["messages"][-1]["content"]
    ]
    assert later and min(later) > risk_at
    assert all(
        "should not run yet" not in call["messages"][-1]["content"] for call in report_llm.calls
    )
    risk_wizard = next(
        i for i, call in enumerate(report_llm.calls)
        if "text2sql 的全部输出" in call["system"] and "问题与风险" in call["system"]
    )
    summary_at = next(i for i, call in enumerate(report_llm.calls) if "你现在写摘要" in call["system"])
    assert risk_at < risk_wizard < summary_at
    review_calls = [call for call in report_llm.calls if call["kind"] == "json" and "报告审核人" in call["system"]]
    assert len(review_calls) == 1 and "1000" in review_calls[0]["system"]
    assert write_calls
    body = write_calls[0]["messages"][-1]["content"]
    assert "1000" in body and "SELECT" not in body
    plan_write = next(call for call in write_calls if "下一周期计划" in call["system"])
    assert "1000" in plan_write["messages"][-1]["content"]
    risk_write = next(call for call in write_calls if "当前章节：问题与风险" in call["system"])
    assert "已写章节" in risk_write["messages"][-1]["content"]
    assert "Report: 1000." in risk_write["messages"][-1]["content"]
    assert "open purchase orders" in risk_write["messages"][-1]["content"]
    summary_write = next(call for call in write_calls if "你现在写摘要" in call["system"])
    assert "未结订单还在" in summary_write["messages"][-1]["content"]
    done = events[-1]
    assert done["report_draft"] is True
    assert done["report_title"] == "订单月报"
    assert done["charts"][0]["title"] == "Order total"
    assert done["charts"][0]["echarts_option"]["series"][0]["data"] == [1000]
    saved = db.session(sid)["history"][-1]
    assert saved["report_draft"] is True
    assert saved["report_title"] == "订单月报"
    assert saved["charts"][0]["echarts_option"]["series"][0]["data"] == [1000]
    assert saved["report_series"][0]["question"] == "order total"
    assert saved["report_series"][0]["rows"][0]["total_amount"] == 1000


def test_badcase_groups_failures_and_keeps_corrections_for_review():
    logs = [
        {"execution_success": False, "error_type": "validation", "error_message": "table is not allowed: sessions", "user_feedback": None},
        {"execution_success": False, "error_type": "execution", "error_message": 'column "nope" does not exist', "generated_sql": "SELECT nope FROM purchase_orders"},
        {"execution_success": True, "user_feedback": "bad", "corrected_sql": "SELECT count(*) FROM purchase_orders", "question": "how many", "generated_sql": "SELECT * FROM purchase_orders"},
        {"execution_success": True, "user_feedback": "good", "question": "fine"},
    ]
    assert classify_error(logs[0]) == "rejected"
    assert classify_error(logs[1]) == "schema_error"
    grouped, report, few = analyze(logs)
    assert len(grouped["rejected"]) == 1 and len(grouped["semantic_error"]) == 1
    assert "3 failed or rated bad" in report
    assert few == [{
        "question": "how many",
        "generated_sql": "SELECT * FROM purchase_orders",
        "corrected_sql": "SELECT count(*) FROM purchase_orders",
    }]


def test_feedback_is_stored_on_the_attempt(db, monkeypatch):
    _models(monkeypatch, [{"tables": []}, {"tables": []}])
    result = answer_question(
        db,
        question="what is the weather", partnership_id="ps-1", brand_id="brand-1", distributor_id="dist-1",
    )
    assert result.ok is False and result.log_id is not None
    db.set_text2sql_feedback(result.log_id, "bad", reason="wrong tables", corrected_sql="SELECT count(*) FROM purchase_orders")
    row = db.list_text2sql_logs("ps-1")[0]
    assert row["user_feedback"] == "bad" and row["corrected_sql"].startswith("SELECT count")
    with pytest.raises(ValueError):
        db.set_text2sql_feedback(result.log_id, "maybe")


def test_queries_run_as_the_reader_and_cannot_write_or_read_files(db):
    db.insert_po(_po(), [], [])
    scope = {"partnership_id": "ps-1", "brand_id": "brand-1", "distributor_id": "dist-1", "user_id": "all"}
    rows = db.run_scoped_read("SELECT current_user AS role", scope_views(), scope)
    assert rows == [{"role": "text2sql_reader"}]
    with pytest.raises(Exception, match=r"(?i)permission denied"):
        db.run_scoped_read("SELECT pg_read_file('/etc/passwd')", scope_views(), scope)
    with pytest.raises(Exception, match=r"(?i)permission denied"):
        db.run_scoped_read(
            "WITH x AS (INSERT INTO purchase_orders (id) VALUES ('hacked') RETURNING id) SELECT id FROM x",
            scope_views(),
            scope,
        )
    assert db.po("po-1") is not None
    assert db.po("hacked") is None


def test_reflection_stops_at_80_and_retries_a_low_score(db, monkeypatch):
    db.insert_po(_po(), [], [])
    pending, _school = _models(monkeypatch, [
        {"tables": ["purchase_orders"]},
        {"sql": "SELECT notes FROM purchase_orders"},
        {"score": 80, "retry_question": "what is the order total"},
        {"tables": ["purchase_orders"]},
        {"sql": "SELECT total_amount FROM purchase_orders"},
    ])
    kept = answer_question(
        db, question="how much did I order?",
        partnership_id="ps-1", brand_id="brand-1", distributor_id="dist-1",
    )
    assert kept.ok and kept.score == 80 and "total_amount" not in kept.rows[0]
    assert len(pending) == 2

    _models(monkeypatch, [
        {"tables": ["purchase_orders"]},
        {"sql": "SELECT notes FROM purchase_orders"},
        {"score": 40, "retry_question": "what is the order total"},
        {"tables": ["purchase_orders"]},
        {"sql": "SELECT total_amount FROM purchase_orders"},
        {"score": 91, "retry_question": ""},
    ])
    retried = answer_question(
        db, question="how much did I order?",
        partnership_id="ps-1", brand_id="brand-1", distributor_id="dist-1",
    )
    assert retried.ok and retried.score == 91 and retried.rows == [{"total_amount": 1000}]


def test_reflection_keeps_the_best_of_three_scores(db, monkeypatch):
    db.insert_po(_po(), [], [])
    _models(monkeypatch, [
        {"tables": ["purchase_orders"]},
        {"sql": "SELECT notes FROM purchase_orders"},
        {"score": 20, "retry_question": "order total"},
        {"tables": ["purchase_orders"]},
        {"sql": "SELECT total_amount FROM purchase_orders"},
        {"score": 70, "retry_question": "how many orders"},
        {"tables": ["purchase_orders"]},
        {"sql": "SELECT count(*) AS n FROM purchase_orders"},
        {"score": 40, "retry_question": "unused"},
    ])
    result = answer_question(
        db, question="how much did I order?",
        partnership_id="ps-1", brand_id="brand-1", distributor_id="dist-1",
    )
    assert result.ok and result.score == 70 and result.rows == [{"total_amount": 1000}]


def test_data_feedback_endpoint_stamps_the_session_and_rejects_another(db, monkeypatch):
    db.insert_po(_po(), [], [])
    db.create_session("sess-rate", "ps-1")
    _models(monkeypatch, [
        {"tables": ["purchase_orders"]},
        {"sql": "SELECT total_amount FROM purchase_orders"},
    ])
    result = answer_question(
        db,
        question="order total",
        partnership_id="ps-1", brand_id="brand-1", distributor_id="dist-1",
        session_id="sess-rate",
    )
    assert result.ok and result.log_id
    db.update_session("sess-rate", history=[
        {"role": "user", "content": "order total"},
        {"role": "assistant", "content": "1000", "data_query": {"log_id": result.log_id, "ok": True}},
    ])
    other = db.create_session("sess-other", "ps-1")["id"]
    client = TestClient(create_app(db, FakeLLM()))
    denied = client.post(f"/api/sessions/{other}/data-feedback", json={
        "log_id": result.log_id, "feedback": "bad",
    })
    assert denied.status_code == 404
    saved = client.post("/api/sessions/sess-rate/data-feedback", json={
        "log_id": result.log_id,
        "feedback": "bad",
        "reason": "used the wrong column",
        "corrected_sql": "SELECT count(*) FROM purchase_orders",
    })
    assert saved.status_code == 200 and saved.json()["feedback"] == "bad"
    row = db.list_text2sql_logs("ps-1")[0]
    assert row["user_feedback"] == "bad" and row["corrected_sql"].startswith("SELECT count")
    history = db.session("sess-rate")["history"]
    assert history[1]["data_query"]["feedback"] == "bad"
