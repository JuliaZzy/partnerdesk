"""Answer one brand question from retrieved passages. This is not an agent.

A later turn is rewritten into a standalone query first. The saved message and
the answer prompt keep the user's words.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ..db import Database
from ..llm import LLM
from ..orchestrator.prompt import FOLLOWUP
from ..orchestrator.speak import answer_directly, one_shot
from .prompt.brand import ANSWER
from .rag import RagUnavailable, evidence_prompt, retrieve, source_label

_SKIP_HISTORY = {"(message)", "(confirm)"}


def _followup_schema() -> dict[str, Any]:
    return {
        "name": "brand_search_query", "strict": True,
        "schema": {
            "type": "object", "additionalProperties": False,
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    }


def _prior_turns(history: list[dict[str, Any]]) -> list[dict[str, str]]:
    turns = []
    for message in history[-8:]:
        role = message.get("role")
        content = str(message.get("content") or "").strip()
        if role in ("user", "assistant") and content and content not in _SKIP_HISTORY:
            turns.append({"role": role, "content": content[:500]})
    return turns


def _search_query(llm: LLM, text: str, history: list[dict[str, Any]]) -> str:
    """Standalone query for retrieval. The first turn is searched as typed.

    A later turn is rewritten with the recent conversation. A failed or empty rewrite
    falls back to the user's words so the answer still runs.
    """
    prior = _prior_turns(history)
    if not prior:
        return text
    recent = "\n".join(f"{turn['role']}: {turn['content']}" for turn in prior)
    try:
        raw = llm.complete_json(
            system=FOLLOWUP,
            messages=[{"role": "user", "content": (
                f"=== RECENT CONVERSATION ===\n{recent}\n\n=== LATEST MESSAGE ===\n{text[:4000]}"
            )}],
            schema=_followup_schema(),
            temperature=0,
            max_tokens=200,
        )
    except Exception:
        return text
    query = str(raw.get("query") or "").strip()
    return query[:500] if query else text


def _with_sources(db: Database, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Number retrieved passages and name the file. The model cites [1]; the client shows `source`."""
    named = []
    for n, hit in enumerate(hits, start=1):
        doc_id = str(hit.get("doc") or "")
        row = db.document(doc_id) if doc_id else None
        file_name = row.file_name if row is not None else None
        title = ((row.title or "").strip() if row is not None else "") or file_name
        named.append({
            **hit,
            "n": n,
            "file_name": file_name,
            "source": source_label(title, hit.get("page"), doc=doc_id or None),
        })
    return named


def answer_in_chat(
    db: Database, llm: LLM, session: dict[str, Any], text: str, history: list[dict[str, Any]], brand_id: str,
) -> Iterator[dict[str, Any]]:
    db.update_session(session["id"], specialist="brand")
    query = _search_query(llm, text, history)
    try:
        hits = retrieve(query, brand_id)
    except RagUnavailable as err:
        yield from one_shot(db, session, str(err))
        return
    named = _with_sources(db, hits)
    yield from answer_directly(
        db, llm, session, text,
        prompt_text=evidence_prompt(text, named),
        system=ANSWER,
        extra={"brand_hits": [
            {
                "n": h["n"], "cid": h.get("cid"), "doc": h.get("doc"), "page": h.get("page"),
                "file_name": h.get("file_name"), "source": h["source"],
            }
            for h in named
        ]},
    )
