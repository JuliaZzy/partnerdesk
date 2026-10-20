"""Write one chat reply into the session and yield the events the desk reads.

Each agent calls this after its own work. The orchestrator uses it for tools
and for a plain answer when the plan names nobody.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ..db import Database
from ..llm import LLM
from .prompt import ANSWER


def one_shot(
    db: Database, session: dict[str, Any], text: str, extra: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    history = [*(session.get("history") or []), {"role": "user", "content": "(message)"}, {"role": "assistant", "content": text}]
    db.update_session(session["id"], history=history[-16:])
    yield {"type": "token", "text": text}
    yield {
        "type": "done", "stage": session.get("stage"), "slots": session.get("slots"), "draft": None,
        "gaps": [], "gaps_detail": [], "advisories": [], "steps": [], "confirm_token": None, "submitted": None,
        **(extra or {}),
    }


def _chat_messages(session: dict[str, Any], prompt: str) -> list[dict[str, Any]]:
    history = list(session.get("history") or [])
    messages = [
        {"role": m.get("role"), "content": str(m.get("content") or "")}
        for m in history[-8:]
        if m.get("role") in ("user", "assistant")
    ]
    messages.append({"role": "user", "content": prompt})
    return messages


def save_reply(
    db: Database, session: dict[str, Any], text: str, reply: str, extra: dict[str, Any] | None,
) -> dict[str, Any]:
    history = list(session.get("history") or [])
    assistant: dict[str, Any] = {"role": "assistant", "content": reply}
    sources = (extra or {}).get("brand_hits")
    if sources:
        assistant["sources"] = sources
    mark = (extra or {}).get("data_query")
    if isinstance(mark, dict) and mark.get("log_id"):
        assistant["data_query"] = {"log_id": mark["log_id"], "ok": bool(mark.get("ok"))}
    charts = (extra or {}).get("charts")
    if isinstance(charts, list) and charts:
        assistant["charts"] = charts
    notes = (extra or {}).get("web_notes")
    if isinstance(notes, list) and notes:
        assistant["web_notes"] = notes
    if (extra or {}).get("report_draft"):
        assistant["report_draft"] = True
    db.update_session(session["id"], history=[*history, {"role": "user", "content": text}, assistant][-16:])
    return {
        "type": "done", "stage": session.get("stage"), "slots": session.get("slots"), "draft": None,
        "gaps": [], "gaps_detail": [], "advisories": [], "steps": [], "confirm_token": None, "submitted": None,
        **(extra or {}),
    }


def answer_directly(
    db: Database, llm: LLM, session: dict[str, Any], text: str, *,
    prompt_text: str | None = None, system: str | None = None, extra: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    parts: list[str] = []
    for chunk in llm.stream_text(system=system or ANSWER, messages=_chat_messages(session, prompt_text or text)):
        parts.append(chunk)
        yield {"type": "token", "text": chunk}
    reply = "".join(parts).strip() or "How can I help?"
    return save_reply(db, session, text, reply, extra)
