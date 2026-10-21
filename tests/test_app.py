"""End-to-end through HTTP with a scripted model: open → plan → PO turn (SSE) → confirm → claim."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.llm import FakeLLM

EXTRACT = {"line_items": [{"reference": "fish oil", "cases": 10, "sku": None}], "discount_kind": "none", "user_confirmed": False, "reply_language": "English"}


def open_meta(tags=("po",), title="10 cases fish oil"):
    return {"tags": list(tags), "title": title}


def plan(*names: str):
    return {
        "tasks": [
            {"id": str(i + 1), "kind": "agent", "name": n, "input": {}, "depends_on": []}
            for i, n in enumerate(names)
        ],
    }


def events(resp) -> list[dict]:
    return [json.loads(l[6:]) for l in resp.text.split("\n\n") if l.startswith("data: ")]


def _of(ev: list[dict], kind: str) -> dict:
    return next(event for event in ev if event["type"] == kind)


def test_full_flow(db):
    llm = FakeLLM(json_replies=[
        open_meta(), plan("po"), dict(EXTRACT),
        plan("po"), {"verdict": "received_ok", "confidence": 0.9},
    ], text_reply="All set — press Confirm.")
    c = TestClient(create_app(db=db, llm=llm))

    assert c.get("/").json()["service"] == "partnerdesk"
    sid = c.post("/api/sessions", json={"partnership_id": "ps-1"}).json()["id"]

    ev = events(c.post(f"/api/sessions/{sid}/message", json={"message": "10 cases of fish oil, no discount"}))
    assert _of(ev, "session") == {"type": "session", "title": "10 cases fish oil", "tags": ["po"]}
    assert [e for e in ev if e["type"] == "status" and e.get("id") == "session"] == []
    assert _of(ev, "plan")["tasks"][0]["name"] == "po"
    plan_steps = [e for e in ev if e["type"] == "status" and e["id"] == "plan"]
    assert plan_steps[0]["state"] == "running" and plan_steps[-1]["state"] == "done"
    assert [e["type"] for e in ev].index("status") < [e["type"] for e in ev].index("plan")
    assert "".join(e["text"] for e in ev if e["type"] == "token") == "All set — press Confirm."
    done = ev[-1]
    assert done["stage"] == "confirm" and done["draft"]["total"] == 1000 and done["confirm_token"]

    r = c.post(f"/api/sessions/{sid}/confirm", json={"token": done["confirm_token"]}).json()
    assert r["state"] == "submitted" and "placed as PO-" in r["message"]
    assert len(c.get("/api/orders").json()) == 1

    ev = events(c.post(f"/api/sessions/{sid}/message", json={"message": "goods arrived, all fine"}))
    assert ev[-1]["po_event"] == "received"
    assert c.get("/api/orders").json()[0]["status"] == "awaiting_payment"


def test_empty_plan_answers_directly(db):
    llm = FakeLLM(json_replies=[open_meta(tags=(), title="hello"), plan()], text_reply="Happy to help — what do you need?")
    c = TestClient(create_app(db=db, llm=llm))
    sid = c.post("/api/sessions", json={}).json()["id"]
    ev = events(c.post(f"/api/sessions/{sid}/message", json={"message": "happy new year!"}))
    assert _of(ev, "session")["type"] == "session"
    assert _of(ev, "plan") == {"type": "plan", "tasks": []}
    assert "Happy to help" in "".join(e["text"] for e in ev if e["type"] == "token")


def test_contract_plan_without_bytes_still_answers(db):
    llm = FakeLLM(json_replies=[
        open_meta(tags=("contract",), title="agreement"),
        plan("contract"),
    ], text_reply="Please attach the agreement PDF.")
    c = TestClient(create_app(db=db, llm=llm))
    sid = c.post("/api/sessions", json={}).json()["id"]
    ev = events(c.post(f"/api/sessions/{sid}/message", json={"message": "new agreement attached", "attachments": ["agreement.pdf"]}))
    assert _of(ev, "plan")["tasks"][0]["name"] == "contract"
    assert "attach" in "".join(e["text"] for e in ev if e["type"] == "token").lower()


def test_model_failure_closes_the_stream_with_an_error_event(db):
    """A failure after a status frame was already flushed must still close the stream."""
    from backend.orchestrator.prompt import PLAN

    class FailPlan(FakeLLM):
        def complete_json(self, *, system, messages, schema=None, temperature=0.0, max_tokens=900):
            if system == PLAN:
                self.calls.append({"kind": "json", "system": system, "messages": messages, "schema": schema})
                raise RuntimeError("model unavailable")
            return super().complete_json(
                system=system, messages=messages, schema=schema, temperature=temperature, max_tokens=max_tokens,
            )

    llm = FailPlan(json_replies=[open_meta()])
    c = TestClient(create_app(db=db, llm=llm))
    sid = c.post("/api/sessions", json={}).json()["id"]
    resp = c.post(f"/api/sessions/{sid}/message", json={"message": "hello"})
    assert resp.status_code == 200
    ev = events(resp)
    assert any(e["type"] == "status" and e["id"] == "plan" and e["state"] == "running" for e in ev)
    assert all(e.get("id") != "session" for e in ev)
    assert ev[-1] == {"type": "error", "message": "The model could not finish this reply."}


def test_a_long_chat_folds_after_the_reply(db, monkeypatch):
    from fastapi import BackgroundTasks

    from backend import memory

    scheduled: list = []
    ran: list = []
    orig_add = BackgroundTasks.add_task

    def spy_add(self, func, *args, **kwargs):
        scheduled.append(func)
        return orig_add(self, func, *args, **kwargs)

    def spy_fold(*args, **kwargs):
        ran.append(1)
        return memory_fold(*args, **kwargs)

    memory_fold = memory.fold_session
    monkeypatch.setattr(BackgroundTasks, "add_task", spy_add)
    monkeypatch.setattr(memory, "fold_session", spy_fold)

    llm = FakeLLM(
        json_replies=[plan(), {"summary": "They ship on Tuesdays"}],
        text_reply="Noted.",
    )
    c = TestClient(create_app(db=db, llm=llm))
    sid = c.post("/api/sessions", json={"partnership_id": "ps-1"}).json()["id"]
    db.update_session(sid, history=[{"role": "user", "content": "Q" * 8000}], title="already open", tags=["chat"])

    resp = c.post(f"/api/sessions/{sid}/message", json={"message": "and one more"})
    assert resp.status_code == 200
    assert scheduled and len(ran) == 1
    saved = db.session(sid)
    assert len(saved["history"]) == 3 and saved["folded_through"] == 1
    assert saved["summary"] == "They ship on Tuesdays"
    assert [m for m in db.memories("ps-1") if m.source == "chat"] == []


def test_empty_message_is_rejected(db):
    c = TestClient(create_app(db=db, llm=FakeLLM()))
    sid = c.post("/api/sessions", json={}).json()["id"]
    assert c.post(f"/api/sessions/{sid}/message", json={"message": "  "}).status_code == 400


def test_delete_session_drops_the_chat_and_keeps_what_it_learned(db):
    from backend.schemas import Memory

    c = TestClient(create_app(db=db, llm=FakeLLM()))
    sid = c.post("/api/sessions", json={"partnership_id": "ps-1"}).json()["id"]
    keep = c.post("/api/sessions", json={"partnership_id": "ps-1"}).json()["id"]
    db.add_memory(Memory(
        partnership_id="ps-1", kind="fact", content="They ship on Tuesdays", source="chat", session_id=sid,
    ))
    assert c.delete(f"/api/sessions/{sid}").status_code == 204
    assert c.get(f"/api/sessions/{sid}").status_code == 404
    assert [row["id"] for row in c.get("/api/sessions?partnership_id=ps-1").json()] == [keep]
    left = db.memories("ps-1")
    assert [(m.content, m.session_id) for m in left] == [("They ship on Tuesdays", None)]
    assert c.delete(f"/api/sessions/{sid}").status_code == 404
