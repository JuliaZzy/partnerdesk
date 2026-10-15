"""Session open + plan — replaces the old classify→route gate."""

from __future__ import annotations

from partnerdesk.llm import FakeLLM
from partnerdesk.orchestrator import TOPIC_KEYS, make_plan, open_session


def test_open_session_returns_title_and_tags():
    llm = FakeLLM(json_replies=[{"tags": ["po"], "title": "20 cases fish oil"}])
    opened = open_session(llm, message="20 cases of fish oil", attachments=[])
    assert opened.tags == ["po"] and opened.title == "20 cases fish oil"
    assert set(llm.calls[0]["schema"]["schema"]["properties"]["tags"]["items"]["enum"]) == set(TOPIC_KEYS)


def test_open_session_drops_unknown_tags_and_defaults_title():
    llm = FakeLLM(json_replies=[{"tags": ["po", "made_up", "data"], "title": "  "}])
    opened = open_session(llm, message="", attachments=["deal.pdf"])
    assert opened.tags == ["po", "data"] and opened.title == "deal.pdf"


def test_open_session_does_not_send_history():
    llm = FakeLLM(json_replies=[{"tags": [], "title": "hello"}])
    open_session(llm, message="hi", attachments=[])
    user = llm.calls[0]["messages"][0]["content"]
    assert "RECENT CONVERSATION" not in user
    assert "=== MESSAGE ===" in user


def test_make_plan_parses_agents_only():
    llm = FakeLLM(json_replies=[{
        "tasks": [
            {"id": "1", "kind": "agent", "name": "po", "input": {}, "depends_on": []},
            {"id": "2", "kind": "tool", "name": "web_search", "input": {"query": "x"}, "depends_on": []},
            {"id": "3", "kind": "agent", "name": "data", "input": {}, "depends_on": []},
        ],
    }])
    tasks = make_plan(
        llm, message="order fish oil", attachments=[], history=[], stage=None, tags=["po"],
    )
    # tools/MCP are not planned — they go through tools= function calling.
    assert [(t.kind, t.name) for t in tasks] == [("agent", "po"), ("agent", "data")]


def test_empty_plan_is_allowed():
    llm = FakeLLM(json_replies=[{"tasks": []}])
    assert make_plan(llm, message="thanks", attachments=[], history=[], stage=None, tags=[]) == []
