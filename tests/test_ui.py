"""The Streamlit page, driven headless with AppTest: type an order, see the draft and the
Confirm button, press it, see the order placed. Same scripted model as the API tests."""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from partnerdesk.llm import FakeLLM

EXTRACT = {"line_items": [{"reference": "fish oil", "cases": 10, "sku": None}], "discount_kind": "none", "user_confirmed": False, "reply_language": "English"}


def cls(topics):
    return {"topics": [{"topic": k, "confidence": c} for k, c in topics], "requests": [], "summary": ""}


UI = Path(__file__).resolve().parent.parent / "partnerdesk" / "ui.py"


def page(db, llm) -> AppTest:
    at = AppTest.from_file(str(UI), default_timeout=30)
    at.session_state["_backend"] = (db, llm)
    return at


def test_order_through_the_page(db):
    llm = FakeLLM(json_replies=[cls([("purchase_order", 0.9)]), dict(EXTRACT)], text_reply="All set — press Confirm.")
    at = page(db, llm).run()
    assert not at.exception
    assert "What would you like" in at.chat_message[0].markdown[0].value

    at.chat_input[0].set_value("10 cases of fish oil, no discount").run()
    assert not at.exception
    texts = [m.value for msg in at.chat_message for m in msg.markdown]
    assert any("press Confirm" in t for t in texts)
    assert at.session_state["stage"] == "confirm" and at.session_state["confirm_token"]
    assert at.session_state["draft"]["total"] == 1000
    assert any("Deep Sea Fish Oil" in str(t.value) for t in at.table)

    confirm = [b for b in at.button if "Confirm" in b.label]
    assert len(confirm) == 1
    confirm[0].click().run()
    assert not at.exception
    assert at.session_state["stage"] == "submitted"
    assert any("placed as PO-" in m.value for msg in at.chat_message for m in msg.markdown)
    assert len(db.list_pos()) == 1


def test_ambiguous_product_offers_buttons(db):
    llm = FakeLLM(json_replies=[cls([("purchase_order", 0.9)]), {**EXTRACT, "line_items": [{"reference": "the serum", "cases": 10, "sku": None}]}], text_reply="Which serum?")
    at = page(db, llm).run()
    at.chat_input[0].set_value("10 cases of the serum, no discount").run()
    assert not at.exception
    labels = [b.label for b in at.button]
    assert "Radiance Serum" in labels and "Radiance Serum Plus" in labels
    assert not [b for b in at.button if "Confirm" in b.label]
