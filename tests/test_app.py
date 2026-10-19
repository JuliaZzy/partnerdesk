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


def test_full_flow(db):
    llm = FakeLLM(json_replies=[
        open_meta(), plan("po"), dict(EXTRACT),
        plan("po"), {"verdict": "received_ok", "confidence": 0.9},
    ], text_reply="All set — press Confirm.")
    c = TestClient(create_app(db=db, llm=llm))

    assert c.get("/").json()["service"] == "partnerdesk"
    sid = c.post("/api/sessions", json={"partnership_id": "ps-1"}).json()["id"]

    ev = events(c.post(f"/api/sessions/{sid}/message", json={"message": "10 cases of fish oil, no discount"}))
    assert ev[0] == {"type": "session", "title": "10 cases fish oil", "tags": ["po"]}
    assert ev[1]["type"] == "plan" and ev[1]["tasks"][0]["name"] == "po"
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
    assert ev[0]["type"] == "session"
    assert ev[1] == {"type": "plan", "tasks": []}
    assert "Happy to help" in "".join(e["text"] for e in ev if e["type"] == "token")


def test_contract_plan_without_bytes_still_answers(db):
    llm = FakeLLM(json_replies=[
        open_meta(tags=("contract",), title="agreement"),
        plan("contract"),
    ], text_reply="Please attach the agreement PDF.")
    c = TestClient(create_app(db=db, llm=llm))
    sid = c.post("/api/sessions", json={}).json()["id"]
    ev = events(c.post(f"/api/sessions/{sid}/message", json={"message": "new agreement attached", "attachments": ["agreement.pdf"]}))
    assert ev[1]["tasks"][0]["name"] == "contract"
    assert "attach" in "".join(e["text"] for e in ev if e["type"] == "token").lower()


def test_empty_message_is_rejected(db):
    c = TestClient(create_app(db=db, llm=FakeLLM()))
    sid = c.post("/api/sessions", json={}).json()["id"]
    assert c.post(f"/api/sessions/{sid}/message", json={"message": "  "}).status_code == 400
