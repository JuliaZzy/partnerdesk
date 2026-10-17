"""Brand questions go to retrieval as typed, then the existing text model answers from the hits.

No Milvus and no embedding endpoint: retrieve is stubbed, chunking is pure.
"""

from __future__ import annotations

from partnerdesk import service
from partnerdesk.brand_knowledge import RagUnavailable, chunks_from_pages, evidence_prompt
from partnerdesk.llm import FakeLLM


def _plan(name: str) -> dict:
    return {"tasks": [{"id": "1", "kind": "agent", "name": name, "input": {}, "depends_on": []}]}


def test_chunks_keep_the_page_and_split_past_350_chars():
    page = "Alpha specs.\n\n" + ("x" * 400)
    chunks = chunks_from_pages("doc-1", [(2, page), (3, "# Title only\n\nBeta line.")])
    assert [c["page"] for c in chunks] == [2, 2, 2, 3]
    assert all(len(c["text"]) <= 350 for c in chunks)
    assert chunks[0]["text"] == "Alpha specs."
    assert chunks[-1]["cid"] == "doc-1#p3-3" and chunks[-1]["text"] == "Beta line."


def test_evidence_prompt_quotes_hits_or_says_there_are_none():
    filled = evidence_prompt("how long?", [{"cid": "d#p1-0", "doc": "d", "page": 1, "text": "8 hours"}])
    assert "[d#p1-0]" in filled and "8 hours" in filled and "how long?" in filled
    assert "(no passages retrieved)" in evidence_prompt("how long?", [])


def test_brand_question_is_retrieved_then_answered_by_the_text_model(db, monkeypatch):
    seen = {}

    def fake_retrieve(question, brand_id):
        seen["q"] = question
        seen["brand"] = brand_id
        return [{"cid": "doc#p1-0", "doc": "doc", "page": 1, "text": "Runs 8 hours at 42 C.", "score": 0.9}]

    monkeypatch.setattr("partnerdesk.orchestrator.retrieve", fake_retrieve)
    sid = service.new_session(db, "ps-1")["id"]
    llm = FakeLLM(
        json_replies=[{"tags": ["brand"], "title": "runtime"}, _plan("brand")],
        text_reply="It runs 8 hours [doc#p1-0].",
    )
    events = list(service.handle_message(db, llm, sid, "How long does it run at 42 C?"))
    assert seen == {"q": "How long does it run at 42 C?", "brand": "brand-1"}
    assert events[1]["tasks"][0]["name"] == "brand"
    told = llm.calls[-1]
    assert told["kind"] == "text" and "passages" in told["system"].lower()
    assert "Runs 8 hours at 42 C." in told["messages"][-1]["content"]
    assert "How long does it run at 42 C?" in told["messages"][-1]["content"]
    assert "8 hours" in "".join(e.get("text", "") for e in events)
    assert events[-1]["brand_hits"][0]["cid"] == "doc#p1-0"
    assert db.session(sid)["specialist"] == "brand"


def test_unconfigured_retrieval_is_told_to_the_user(db, monkeypatch):
    def boom(question, brand_id):
        raise RagUnavailable("EMBEDDING_BASE_URL is not set.")

    monkeypatch.setattr("partnerdesk.orchestrator.retrieve", boom)
    sid = service.new_session(db, "ps-1")["id"]
    llm = FakeLLM(json_replies=[{"tags": ["brand"], "title": "specs"}, _plan("brand")], text_reply="should not be used")
    events = list(service.handle_message(db, llm, sid, "What is the shelf life?"))
    assert "EMBEDDING_BASE_URL" in "".join(e.get("text", "") for e in events)
    assert not any(c["kind"] == "text" for c in llm.calls)
