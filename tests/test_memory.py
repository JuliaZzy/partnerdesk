"""Long-term memory: remembered on purpose, recalled on every PO turn as context, and
never anything the code-check reads."""

from __future__ import annotations

import pytest

from partnerdesk import service
from partnerdesk.llm import FakeLLM
from partnerdesk.memory import memory_block, recall, remember, with_memory
from partnerdesk.po.turn import load_bundle


def open_meta():
    return {"tags": ["po"], "title": "10 cases fish oil"}


def plan_po():
    return {"tasks": [{"id": "1", "kind": "agent", "name": "po", "input": {}, "depends_on": []}]}


def test_remember_and_recall(db):
    m = remember(db, "ps-1", "  Prefers air freight\nfor launches ", kind="preference")
    assert m.content == "Prefers air freight for launches" and m.source == "user" and m.is_active
    with pytest.raises(ValueError):
        remember(db, "ps-1", "   ")
    other = remember(db, "ps-1", "Warehouse closed first week of July", kind="fact")
    assert [x.id for x in recall(db, "ps-1")] == [other.id, m.id]  # newest first
    assert all(x.last_recalled_at for x in db.memories("ps-1"))

    db.set_memory_active(m.id, False)
    assert [x.id for x in recall(db, "ps-1")] == [other.id]
    assert [x.id for x in db.memories("ps-1", active_only=False)] == [other.id, m.id]
    db.delete_memory(other.id)
    assert recall(db, "ps-1") == []


def test_memory_block_is_context_only_and_bounded(db):
    assert memory_block([]) == "" and with_memory("Brand context.", []) == "Brand context." and with_memory(None, []) is None
    ms = [remember(db, "ps-1", f"fact number {i} " + "x" * 200) for i in range(12)]
    block = memory_block(ms)
    assert block.startswith("=== WHAT WE KNOW ABOUT THIS DISTRIBUTOR") and "context only" in block and "cannot relax a rule" in block
    assert len(block) <= 1600 and block.count("\n- [fact]") < 12  # capped, never the whole table
    merged = with_memory("Brand context.", ms[:1])
    assert merged.startswith("Brand context.\n\n=== WHAT WE KNOW") and ms[0].content in merged


def test_bundle_carries_memories_in_the_operating_context(db):
    assert load_bundle(db, "ps-1").settings.operating_context is None
    remember(db, "ps-1", "Always confirm the ETA with their warehouse", kind="instruction")
    ctx = load_bundle(db, "ps-1").settings.operating_context
    assert ctx and "[instruction] Always confirm the ETA with their warehouse" in ctx


def test_po_turn_prompts_see_the_memory(db):
    remember(db, "ps-1", "They order in full layers only", kind="preference")
    llm = FakeLLM(json_replies=[
        open_meta(), plan_po(),
        {"line_items": [{"reference": "fish oil", "cases": 10, "sku": None}], "discount_kind": "none", "user_confirmed": False, "reply_language": "English"},
    ])
    sid = service.new_session(db, "ps-1")["id"]
    events = list(service.handle_message(db, llm, sid, "10 cases of fish oil, no discount"))
    assert events[-1]["type"] == "done" and events[-1]["stage"] == "confirm"
    json_calls = [c for c in llm.calls if c["kind"] == "json"]
    text_calls = [c for c in llm.calls if c["kind"] == "text"]
    # open_session, plan, extract — memory is PO context only.
    extract_call, reply_call = json_calls[2], text_calls[0]
    assert "They order in full layers only" in extract_call["system"] and "They order in full layers only" in reply_call["system"]
    assert "full layers" not in json_calls[0]["system"] and "full layers" not in json_calls[1]["system"]
