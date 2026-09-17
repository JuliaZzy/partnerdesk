"""End-to-end through HTTP with a scripted model: route → PO turn (SSE) → confirm → claim."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from partnerdesk.app import create_app
from partnerdesk.llm import FakeLLM


def cls(topics):
    return {"topics": [{"topic": k, "confidence": c} for k, c in topics], "requests": [], "summary": ""}


EXTRACT = {"line_items": [{"reference": "fish oil", "cases": 10, "sku": None}], "discount_kind": "none", "user_confirmed": False, "reply_language": "English"}


def events(resp) -> list[dict]:
    return [json.loads(l[6:]) for l in resp.text.split("\n\n") if l.startswith("data: ")]


def test_full_flow(db):
    llm = FakeLLM(json_replies=[
        cls([("purchase_order", 0.9)]), dict(EXTRACT),                       # turn 1: route + extract
        cls([("purchase_order", 0.9)]), {"verdict": "received_ok", "confidence": 0.9},  # after submit: route + receipt
    ], text_reply="All set — press Confirm.")
    c = TestClient(create_app(db=db, llm=llm))

    assert c.get("/").json()["service"] == "partnerdesk"
    sid = c.post("/api/sessions", json={"partnership_id": "ps-1"}).json()["id"]

    ev = events(c.post(f"/api/sessions/{sid}/message", json={"message": "10 cases of fish oil, no discount"}))
    assert ev[0] == {"type": "route", "specialist": "po", "reason": "purchase_order (0.90)"}
    assert "".join(e["text"] for e in ev if e["type"] == "token") == "All set — press Confirm."
    done = ev[-1]
    assert done["stage"] == "confirm" and done["draft"]["total"] == 1000 and done["confirm_token"]

    r = c.post(f"/api/sessions/{sid}/confirm", json={"token": done["confirm_token"]}).json()
    assert r["state"] == "submitted" and "placed as PO-" in r["message"]
    assert len(c.get("/api/orders").json()) == 1

    ev = events(c.post(f"/api/sessions/{sid}/message", json={"message": "goods arrived, all fine"}))
    assert ev[-1]["po_event"] == "received"
    assert c.get("/api/orders").json()[0]["status"] == "awaiting_payment"


def test_abstain_and_stubs(db):
    llm = FakeLLM(json_replies=[cls([("general_relationship", 0.9)]), cls([("contract_terms", 0.9)])])
    c = TestClient(create_app(db=db, llm=llm))
    sid = c.post("/api/sessions", json={}).json()["id"]
    ev = events(c.post(f"/api/sessions/{sid}/message", json={"message": "happy new year!"}))
    assert ev[0]["specialist"] is None and "leave it for a person" in ev[1]["text"]
    ev = events(c.post(f"/api/sessions/{sid}/message", json={"message": "new agreement attached", "attachments": ["agreement.pdf"]}))
    assert ev[0]["specialist"] == "contract"


def test_empty_message_is_rejected(db):
    c = TestClient(create_app(db=db, llm=FakeLLM()))
    sid = c.post("/api/sessions", json={}).json()["id"]
    assert c.post(f"/api/sessions/{sid}/message", json={"message": "  "}).status_code == 400
