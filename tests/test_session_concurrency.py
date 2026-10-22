"""Two chats at once, and one chat's turns in order.

The overlap is the model call: that is where a second turn would read a
history the first has not saved yet.
"""

from __future__ import annotations

import threading

from backend import service
from backend.llm import FakeLLM


def test_two_sessions_overlap_on_the_model(db):
    state = {"inside": 0, "max": 0}
    mu = threading.Lock()
    gate = threading.Barrier(2)
    errors: list[BaseException] = []

    class Overlap(FakeLLM):
        def complete_json(self, **kwargs):
            with mu:
                state["inside"] += 1
                state["max"] = max(state["max"], state["inside"])
            try:
                gate.wait(timeout=3)
                return super().complete_json(**kwargs)
            finally:
                with mu:
                    state["inside"] -= 1

    def run(sid: str, llm: FakeLLM) -> None:
        try:
            list(service.handle_message(db, llm, sid, "hello"))
        except BaseException as err:
            gate.abort()
            errors.append(err)

    first = service.new_session(db, "ps-1")["id"]
    second = service.new_session(db, "ps-1")["id"]
    one = Overlap(json_replies=[{"tags": [], "title": "one"}, {"tasks": []}])
    two = Overlap(json_replies=[{"tags": [], "title": "two"}, {"tasks": []}])
    threads = [
        threading.Thread(target=run, args=(first, one)),
        threading.Thread(target=run, args=(second, two)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert errors == []
    assert state["max"] == 2
    assert [m["content"] for m in db.session(first)["history"] if m["role"] == "user"] == ["hello"]
    assert [m["content"] for m in db.session(second)["history"] if m["role"] == "user"] == ["hello"]


def test_naming_a_new_chat_does_not_block_the_plan(db):
    """The title call runs beside the plan. The plan's model call starts first."""
    from backend.llm.types import AssistantTurn

    order: list[str] = []
    plan_entered = threading.Event()

    class Live:
        def complete_json(self, *, system, messages, schema=None, temperature=0.0, max_tokens=900):
            if system.startswith("你在为一场"):
                order.append("open-start")
                assert plan_entered.wait(2), "plan did not start while the title was in flight"
                order.append("open-end")
                return {"tags": ["data"], "title": "订单总额"}
            order.append("plan")
            plan_entered.set()
            return {"tasks": []}

        def stream_text(self, *, system, messages, temperature=0.7, max_tokens=300):
            yield "Hello"

        def complete(self, *, system, messages, tools=None, temperature=0.0, max_tokens=900):
            return AssistantTurn()

    sid = service.new_session(db, "ps-1")["id"]
    events = list(service.handle_message(db, Live(), sid, "how much did I order"))
    assert order.index("plan") < order.index("open-end")
    assert {"type": "session", "title": "订单总额", "tags": ["data"]} in events
    assert db.session(sid)["title"] == "订单总额"
    assert db.session(sid)["tags"] == ["data"]


def test_one_session_finishes_a_turn_before_the_next_starts(db):
    state = {"inside": 0, "max": 0}
    mu = threading.Lock()
    errors: list[BaseException] = []

    class Slow(FakeLLM):
        def complete_json(self, **kwargs):
            with mu:
                state["inside"] += 1
                state["max"] = max(state["max"], state["inside"])
            try:
                threading.Event().wait(0.15)
                return super().complete_json(**kwargs)
            finally:
                with mu:
                    state["inside"] -= 1

    sid = service.new_session(db, "ps-1")["id"]
    llm = Slow(json_replies=[
        {"tags": [], "title": "a"}, {"tasks": []}, {"tasks": []},
    ], text_reply="Hello")

    def run(text: str) -> None:
        try:
            list(service.handle_message(db, llm, sid, text))
        except BaseException as err:
            errors.append(err)

    threads = [threading.Thread(target=run, args=(text,)) for text in ("one", "two")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert errors == []
    assert state["max"] == 1
    history = db.session(sid)["history"]
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]
    users = [m["content"] for m in history if m["role"] == "user"]
    assert sorted(users) == ["one", "two"]
