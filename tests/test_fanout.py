"""Several tasks in one turn share one reply. Redis holds the lock and the results."""

from __future__ import annotations

import threading

from backend import service
from backend.llm import FakeLLM
from backend.redis_store import begin_turn, drop_turn, fx_drop, fx_put, load_results, store_result
from backend.schemas import PurchaseOrder
from backend.services.chat_run import work_chat
from backend.tools.exchange_rate import quote


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


def test_one_reply_covers_contract_and_data(db, monkeypatch):
    db.insert_po(PurchaseOrder(**dict(
        id="po-fan", po_number="PO-FAN", brand_id="brand-1", distributor_id="dist-1", partnership_id="ps-1",
        status="submitted", currency="USD", payment_terms="net_30", incoterms="FOB", shipping_method="sea",
        eta_date=None, ship_to_country="US", notes=None, discount_amount=None, discount_percentage=None,
        subtotal_amount=1000, total_amount=1000,
    )), [], [])
    db.upsert_user("u-a", "dist-1", "Ada")
    sid = db.create_session("sess-fan", "ps-1", "u-a")["id"]
    from tests.test_text2sql import _models

    _models(monkeypatch, [
        {"tables": ["purchase_orders"]},
        {"sql": "SELECT total_amount FROM purchase_orders"},
    ])
    llm = FakeLLM(json_replies=[
        {"tags": ["data", "contract"], "title": "一起"},
        {"tasks": [
            {"id": "c", "kind": "agent", "name": "contract", "input": {}, "depends_on": []},
            {"id": "d", "kind": "agent", "name": "data", "input": {"question": "order total"}, "depends_on": []},
        ]},
    ], text_reply="合同还没有文件。总额 1000。")
    events = list(service.handle_message(db, llm, sid, "查一下订单总额，顺便看看合同"))
    assert work_chat(db, llm, events[0]["run_id"]) is True
    history = db.session(sid)["history"]
    assert [message["role"] for message in history] == ["user", "assistant"]
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
