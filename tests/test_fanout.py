"""Several tasks in one turn share one reply. Redis holds the lock and the results."""

from __future__ import annotations

import threading

from backend import service
from backend.llm import FakeLLM
from backend.orchestrator.gather import scheduled
from backend.orchestrator.turn import PlanTask
from backend.redis_store import begin_turn, drop_turn, fx_drop, fx_put, load_results, store_result
from backend.schemas import PurchaseOrder
from backend.tools.exchange_rate import quote


def test_a_later_tool_uses_the_earlier_result_not_the_planned_placeholder():
    from backend.orchestrator.gather import _arguments_for

    search = {
        "name": "web_search", "ok": True,
        "payload": {"tool": "web_search", "result": {"ok": True, "data": {
            "results": [{"title": "北京", "snippet": "10/23 16/7°C"}],
        }}},
    }
    translate = PlanTask(
        id="t3", kind="tool", name="translate", depends_on=["t1"],
        input={"text": "北京天气和特色小吃推荐", "target": "en"},
    )
    args = _arguments_for(translate, [search])
    assert "16/7°C" in args["text"]
    assert args["target"] == "en"
    mailed = PlanTask(
        id="t4", kind="tool", name="send_email", depends_on=["t3"],
        input={"to": "julia_zzy@126.com", "subject": "北京天气", "body": "请查收北京天气和特色小吃推荐（英文版）。"},
    )
    body = _arguments_for(mailed, [{
        "name": "translate", "ok": True,
        "payload": {"result": {"ok": True, "data": {"translation": "Beijing, 16/7°C."}}},
    }])
    assert body["body"] == "Beijing, 16/7°C."
    assert body["to"] == "julia_zzy@126.com"


def test_each_parallel_task_has_its_own_running_line():
    from backend.orchestrator.gather import _running_line

    assert _running_line(PlanTask(id="c", kind="agent", name="contract")) == "正在解析合同"
    assert _running_line(PlanTask(id="t", kind="tool", name="translate")) == "正在翻译"
    assert _running_line(PlanTask(id="w", kind="tool", name="web_search")) == "正在进行网络搜索"


def test_a_report_keeps_its_own_data_task_as_a_hint():
    data = PlanTask(id="d", kind="agent", name="data", depends_on=[])
    report = PlanTask(id="r", kind="agent", name="report", depends_on=["d"])
    assert [task.name for task in scheduled([data, report])] == ["report"]
    own = PlanTask(id="q", kind="agent", name="data", depends_on=[])
    assert [task.name for task in scheduled([own, report])] == ["data", "report"]


def test_results_arrive_in_any_order_and_read_back_together():
    begin_turn("sess-redis", "turn-1", ["a", "b"])
    try:
        assert store_result("sess-redis", "turn-1", "b", {"id": "b", "ok": True, "payload": {"n": 2}}) == 1
        assert store_result("sess-redis", "turn-1", "a", {"id": "a", "ok": True, "payload": {"n": 1}}) == 0
        loaded = load_results("sess-redis", "turn-1")
        assert loaded["a"]["payload"]["n"] == 1 and loaded["b"]["payload"]["n"] == 2
    finally:
        drop_turn("sess-redis", "turn-1")


def test_cached_exchange_rate_skips_the_network(monkeypatch):
    fx_drop("USD", "EUR")
    fx_put("USD", "EUR", rate=2, date="2026-01-01", source="test")

    def boom(*_a, **_k):
        raise AssertionError("network")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    try:
        quoted = quote("USD", "EUR", amount=3)
        assert quoted["rate"] == 2 and quoted["converted"] == 6
    finally:
        fx_drop("USD", "EUR")


def test_one_reply_covers_contract_and_data(db):
    db.insert_po(PurchaseOrder(**dict(
        id="po-fan", po_number="PO-FAN", brand_id="brand-1", distributor_id="dist-1", partnership_id="ps-1",
        status="submitted", currency="USD", payment_terms="net_30", incoterms="FOB", shipping_method="sea",
        eta_date=None, ship_to_country="US", notes=None, discount_amount=None, discount_percentage=None,
        subtotal_amount=1000, total_amount=1000,
    )), [], [])
    sid = db.create_session("sess-fan", "ps-1")["id"]
    llm = FakeLLM(json_replies=[
        {"tags": ["data", "contract"], "title": "一起"},
        {"tasks": [
            {"id": "c", "kind": "agent", "name": "contract", "input": {}, "depends_on": []},
            {"id": "d", "kind": "agent", "name": "data", "input": {"question": "order total"}, "depends_on": []},
        ]},
        {"tables": ["purchase_orders"]},
        {"sql": "SELECT total_amount FROM purchase_orders"},
    ], text_reply="合同还没有文件。总额 1000。")
    events = list(service.handle_message(db, llm, sid, "查一下订单总额，顺便看看合同"))
    running = [event.get("title") for event in events if event.get("type") == "status" and event.get("state") == "running"]
    assert "正在解析合同" in running and "正在查数" in running
    assert sum(event.get("type") == "done" for event in events) == 1
    text_call = next(call for call in llm.calls if call["kind"] == "text")
    body = text_call["messages"][-1]["content"]
    assert "1000" in body and "need_file" in body
    history = db.session(sid)["history"]
    assert [message["role"] for message in history] == ["user", "assistant"]
    assert history[-1]["combined"] is True
    assert history[-1]["content"] == "合同还没有文件。总额 1000。"


def test_session_lock_is_exclusive():
    from backend.redis_store import hold_session

    order: list[str] = []
    holding = threading.Event()
    errors: list[BaseException] = []

    def first() -> None:
        try:
            with hold_session("sess-lock-test"):
                order.append("a")
                holding.set()
                threading.Event().wait(0.1)
        except BaseException as err:
            errors.append(err)

    def second() -> None:
        try:
            assert holding.wait(2)
            with hold_session("sess-lock-test"):
                order.append("b")
        except BaseException as err:
            errors.append(err)

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert errors == []
    assert order == ["a", "b"]
