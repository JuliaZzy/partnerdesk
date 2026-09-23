"""The Streamlit page, driven headless with AppTest: type an order, see the draft and the
Confirm button, press it, see the order placed. Same scripted model as the API tests."""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from partnerdesk.config import CHAT_MODELS
from partnerdesk.llm import FakeLLM

EXTRACT = {"line_items": [{"reference": "fish oil", "cases": 10, "sku": None}], "discount_kind": "none", "user_confirmed": False, "reply_language": "English"}


def cls(topics):
    return {"topics": [{"topic": k, "confidence": c} for k, c in topics], "requests": [], "summary": ""}


UI = Path(__file__).resolve().parent.parent / "partnerdesk" / "ui.py"


def page(db, llm) -> AppTest:
    at = AppTest.from_file(str(UI), default_timeout=30)
    at.session_state["_backend"] = (db, llm)
    return at


def md(at: AppTest) -> str:
    return "\n".join(m.value for m in at.markdown)


def test_order_through_the_page(db):
    llm = FakeLLM(json_replies=[cls([("purchase_order", 0.9)]), dict(EXTRACT)], text_reply="All set — press Confirm.")
    at = page(db, llm).run()
    assert not at.exception
    assert "What would you like" in md(at)

    at.chat_input[0].set_value("10 cases of fish oil, no discount").run()
    assert not at.exception
    assert "press Confirm" in md(at)
    assert at.session_state["stage"] == "confirm" and at.session_state["confirm_token"]
    assert at.session_state["draft"]["total"] == 1000
    assert any("Deep Sea Fish Oil" in str(t.value) for t in at.table)

    confirm = [b for b in at.button if "Confirm" in b.label]
    assert len(confirm) == 1
    assert not confirm[0].proto.disabled
    confirm[0].click().run()
    assert not at.exception
    assert at.session_state["stage"] == "submitted"
    assert "placed as PO-" in md(at)
    assert len(db.list_pos()) == 1


def test_model_picker_lists_campus_models(db):
    at = page(db, FakeLLM()).run()
    assert not at.exception
    box = at.selectbox[0]
    shorts = [m.short for m in CHAT_MODELS]
    ids = [m.id for m in CHAT_MODELS]
    assert all(x in box.options for x in ids) or all(x in box.options for x in shorts)
    box.select("qwen").run()
    assert not at.exception
    assert at.session_state["llm_model"] == "qwen"


def test_new_chat_keeps_the_previous_one(db):
    llm = FakeLLM(json_replies=[cls([("purchase_order", 0.9)]), dict(EXTRACT)], text_reply="All set — press Confirm.")
    at = page(db, llm).run()
    at.chat_input[0].set_value("10 cases of fish oil, no discount").run()
    first = at.session_state["sid"]
    assert "press Confirm" in md(at)

    at.button(key="new-chat").click().run()
    assert not at.exception
    second = at.session_state["sid"]
    assert second != first
    assert at.session_state["chat_order"][0] == second
    assert "What would you like" in md(at)
    assert "press Confirm" not in md(at)

    order_before = list(at.session_state["chat_order"])
    at.button(key=f"open-{first}").click().run()
    assert not at.exception
    assert at.session_state["sid"] == first
    assert at.session_state["chat_order"] == order_before
    assert at.session_state["chat_order"][0] == second
    assert "press Confirm" in md(at)


def test_confirm_disabled_until_inputs_checked(db):
    at = page(db, FakeLLM()).run()
    at.session_state["stage"] = "confirm"
    at.session_state["confirm_token"] = "tok"
    at.session_state["steps"] = [
        {"title": "Understand request", "status": "pending"},
        {"title": "Check inputs", "status": "pending"},
        {"title": "Confirm draft", "status": "needs-you"},
        {"title": "Create & submit order", "status": "pending"},
    ]
    at.run()
    assert not at.exception
    confirm = [b for b in at.button if "Confirm" in b.label]
    assert len(confirm) == 1 and confirm[0].proto.disabled


def test_ambiguous_product_offers_buttons(db):
    llm = FakeLLM(json_replies=[cls([("purchase_order", 0.9)]), {**EXTRACT, "line_items": [{"reference": "the serum", "cases": 10, "sku": None}]}], text_reply="Which serum?")
    at = page(db, llm).run()
    at.chat_input[0].set_value("10 cases of the serum, no discount").run()
    assert not at.exception
    labels = [b.label for b in at.button]
    assert "Radiance Serum" in labels and "Radiance Serum Plus" in labels
    assert not [b for b in at.button if "Confirm" in b.label]
