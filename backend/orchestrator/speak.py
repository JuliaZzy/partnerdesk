"""Write one chat reply into the session and yield the events the desk reads.

Each agent calls this after its own work. The orchestrator uses it for tools
and for a plain answer when the plan names nobody.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ..db import Database
from ..llm import LLM
from ..memory import attach, prompt_context, prompt_turns, record_history, standing_block, summary_block
from .prompt import ANSWER


def attach_model_json(message: dict[str, Any], llm: LLM | None) -> None:
    """Copy this turn's JSON objects onto the assistant message. The list on the client is replaced next turn."""
    from .status import current_activity

    outputs = getattr(llm, "model_outputs", None)
    if isinstance(outputs, list) and outputs:
        message["model_json"] = list(outputs)
    activity = current_activity()
    if activity:
        message["activity"] = activity


def one_shot(
    db: Database, session: dict[str, Any], text: str, extra: dict[str, Any] | None = None,
    llm: LLM | None = None,
) -> Iterator[dict[str, Any]]:
    assistant: dict[str, Any] = {"role": "assistant", "content": text}
    attach_model_json(assistant, llm)
    history = [*(session.get("history") or []), {"role": "user", "content": "(message)"}, assistant]
    record_history(db, session, history)
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
        for m in prompt_turns(history, int(session.get("folded_through") or 0))
        if m.get("role") in ("user", "assistant")
    ]
    messages.append({"role": "user", "content": prompt})
    return messages


def save_reply(
    db: Database, session: dict[str, Any], text: str, reply: str, extra: dict[str, Any] | None,
    llm: LLM | None = None,
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
    series = (extra or {}).get("report_series")
    if isinstance(series, list) and series:
        assistant["report_series"] = series
    attach_model_json(assistant, llm)
    record_history(db, session, [*history, {"role": "user", "content": text}, assistant], llm=llm)
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
    system = attach(system or ANSWER, prompt_context(
        standing_block(db, session["partnership_id"]), summary_block(session.get("summary")),
    ))
    for chunk in llm.stream_text(system=system, messages=_chat_messages(session, prompt_text or text)):
        parts.append(chunk)
        yield {"type": "token", "text": chunk}
    reply = "".join(parts).strip() or "How can I help?"
    return save_reply(db, session, text, reply, extra, llm=llm)
