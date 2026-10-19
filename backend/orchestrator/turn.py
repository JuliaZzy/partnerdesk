"""Chat entry: session_open once, then agents via PLAN, tools/MCP via native tools=.

Tools and MCP are injected with `tools=` / tool_choice=auto — the model chooses which
to call and fills arguments (same pattern as OpenAI function calling). PLAN only
schedules agents (po / contract / report / brand / data).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from ..brand_knowledge import RagUnavailable, evidence_prompt, retrieve
from ..brand_knowledge.prompt.brand import ANSWER as BRAND_ANSWER
from ..brand_knowledge.rag import source_label
from ..contract import file_contract
from ..db import Database
from ..llm import LLM, AssistantTurn
from ..po.claims import handle_post_submit
from ..po.turn import load_bundle, run_po_turn
from ..report import draft_report
from ..text2sql import answer_question
from ..text2sql.prompt.data_query import ANSWER as DATA_ANSWER
from ..tools import all_schemas, all_tools
from .prompt.orchestrator import ANSWER, FOLLOWUP, PLAN, REACT
from .prompt.session_open import OPEN

MAX_IMAGES = 3
MAX_TOOL_ROUNDS = 4

TOPIC_KEYS = ("po", "contract", "report", "brand", "data")
WRITE_AGENTS = frozenset({"po", "contract", "report"})


@dataclass(frozen=True)
class SessionOpen:
    title: str
    tags: list[str]


@dataclass
class PlanTask:
    id: str
    kind: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)


def session_open_schema() -> dict[str, Any]:
    return {
        "name": "session_open", "strict": True,
        "schema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "tags": {"type": "array", "items": {"type": "string", "enum": list(TOPIC_KEYS)}},
                "title": {"type": "string"},
            },
            "required": ["tags", "title"],
        },
    }


def followup_schema() -> dict[str, Any]:
    return {
        "name": "brand_search_query", "strict": True,
        "schema": {
            "type": "object", "additionalProperties": False,
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    }


def plan_schema() -> dict[str, Any]:
    return {
        "name": "orchestrator_plan", "strict": True,
        "schema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "tasks": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "id": {"type": "string"},
                        "kind": {"type": "string", "enum": ["agent"]},
                        "name": {"type": "string"},
                        "input": {"type": "object"},
                        "depends_on": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["id", "kind", "name", "input", "depends_on"],
                }},
            },
            "required": ["tasks"],
        },
    }


def open_session(llm: LLM, *, message: str, attachments: list[str]) -> SessionOpen:
    """First-turn only: tags + title. No history — the session does not have one yet."""
    user = (
        f"=== ATTACHMENTS ===\n{', '.join(attachments) or '(none)'}\n\n"
        f"=== MESSAGE ===\n{message[:4000] or '(no text)'}"
    )
    raw = llm.complete_json(
        system=OPEN, messages=[{"role": "user", "content": user}],
        schema=session_open_schema(), temperature=0, max_tokens=200,
    )
    allowed = set(TOPIC_KEYS)
    tags = [t for t in (raw.get("tags") or []) if isinstance(t, str) and t in allowed]
    title = str(raw.get("title") or "").strip() or (attachments[0] if attachments else "New chat")
    return SessionOpen(title=title[:80], tags=tags)


def make_plan(
    llm: LLM, *, message: str, attachments: list[str], history: list[dict[str, str]],
    stage: str | None, tags: list[str],
) -> list[PlanTask]:
    recent = "\n".join(f"{m['role']}: {str(m['content'])[:500]}" for m in history[-8:])
    user = (
        f"=== SESSION TAGS ===\n{', '.join(tags) or '(none)'}\n"
        f"=== STAGE ===\n{stage or '(none)'}\n\n"
        f"=== RECENT CONVERSATION ===\n{recent or '(none)'}\n\n"
        f"=== ATTACHMENTS ===\n{', '.join(attachments) or '(none)'}\n\n"
        f"=== MESSAGE ===\n{message[:4000] or '(no text)'}"
    )
    raw = llm.complete_json(
        system=PLAN, messages=[{"role": "user", "content": user}],
        schema=plan_schema(), temperature=0, max_tokens=600,
    )
    out: list[PlanTask] = []
    for t in raw.get("tasks") or []:
        if not isinstance(t, dict):
            continue
        kind, name = str(t.get("kind") or "agent"), str(t.get("name") or "")
        if kind != "agent" or name not in TOPIC_KEYS:
            continue
        deps = [d for d in (t.get("depends_on") or []) if isinstance(d, str)]
        inp = t.get("input") if isinstance(t.get("input"), dict) else {}
        tid = str(t.get("id") or f"agent-{name}-{len(out)}")
        out.append(PlanTask(id=tid, kind="agent", name=name, input=inp, depends_on=deps))
    return out


def _run_one_tool(
    call: Any, runners: dict[str, Any], llm: LLM, context: dict[str, Any],
) -> dict[str, Any]:
    fn = runners.get(call.name)
    if fn is None:
        result: dict[str, Any] = {"ok": False, "data": None, "error": f"unknown tool {call.name}"}
    else:
        try:
            result = fn(call.arguments, llm=llm, context=context)
        except Exception as err:
            result = {"ok": False, "data": None, "error": str(err)}
    return {
        "role": "tool",
        "tool_call_id": call.id,
        "content": json.dumps(result, ensure_ascii=False),
    }


def run_tool_calls(
    llm: LLM, *, text: str, history: list[dict[str, Any]], extra: dict[str, Any] | None = None,
    include_mcp: bool | None = None,
) -> str | None:
    """Native function-calling loop (tools= / tool_choice=auto).

    One loop covers single, chain, and parallel:
    - one tool_call → single
    - several tool_calls in the same assistant turn → run in parallel (ThreadPool)
    - tools again after results → chain (next while iteration)

    The model chooses which; we do not ask it to pick a "strategy" first.
    Returns final prose, or None if no tool was used.
    """
    schemas = all_schemas(include_mcp=include_mcp)
    runners = all_tools(include_mcp=include_mcp)
    if not schemas:
        return None
    messages: list[dict[str, Any]] = [{"role": "user", "content": text}]
    context = {"history": history, **(extra or {})}
    used = False
    last: AssistantTurn | None = None
    for _ in range(MAX_TOOL_ROUNDS):
        last = llm.complete(system=REACT, messages=messages, tools=schemas)
        if not last.tool_calls:
            if used:
                return (last.content or "").strip() or "Done."
            return None
        used = True
        messages.append(last.as_message())
        calls = last.tool_calls
        if len(calls) == 1:
            messages.append(_run_one_tool(calls[0], runners, llm, context))
        else:
            # Same-turn fan-out: model asked for several tools at once → parallel.
            by_id: dict[str, dict[str, Any]] = {}
            with ThreadPoolExecutor(max_workers=min(5, len(calls))) as pool:
                futs = {pool.submit(_run_one_tool, c, runners, llm, context): c.id for c in calls}
                for fut in as_completed(futs):
                    msg = fut.result()
                    by_id[msg["tool_call_id"]] = msg
            # Preserve the model's call order when appending (API is happier that way).
            messages.extend(by_id[c.id] for c in calls)
    return (last.content if last else None) or "Done."


def _one_shot(db: Database, session: dict[str, Any], text: str, extra: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
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


def _query_mark(result: Any) -> dict[str, Any]:
    return {"ok": result.ok, "row_count": result.row_count, "error": result.error, "log_id": result.log_id}


def _save_reply(
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


def _answer_directly(
    db: Database, llm: LLM, session: dict[str, Any], text: str, *,
    prompt_text: str | None = None, system: str | None = None, extra: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    parts: list[str] = []
    for chunk in llm.stream_text(system=system or ANSWER, messages=_chat_messages(session, prompt_text or text)):
        parts.append(chunk)
        yield {"type": "token", "text": chunk}
    reply = "".join(parts).strip() or "How can I help?"
    yield _save_reply(db, session, text, reply, extra)


def _contract_prompt(text: str, filed: dict[str, Any]) -> str:
    """The chat model quotes this table. It does not recompute the filing."""
    return (
        f"{text}\n\n"
        "=== CONTRACT FILING (from code — quote these figures, do not recompute or invent terms) ===\n"
        f"{json.dumps(filed, ensure_ascii=False)}\n"
        "If status is need_file, ask for the agreement file. If failed, say what went wrong. "
        "If filed, tell them what was stored, including skipped rows."
    )


_SKIP_HISTORY = {"(message)", "(confirm)"}


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
            schema=followup_schema(),
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


def _agent_names(tasks: list[PlanTask]) -> set[str]:
    return {t.name for t in tasks if t.kind == "agent"}


def _task_question(tasks: list[PlanTask], name: str, fallback: str) -> str:
    """The planner may put the question on the task. The user's words are the fallback."""
    for task in tasks:
        if task.name != name:
            continue
        raw = task.input.get("question") or task.input.get("text") or ""
        if isinstance(raw, str) and raw.strip():
            return raw.strip()[:2000]
    return fallback


def _data_prompt(question: str, result: Any) -> str:
    payload = {"ok": result.ok, "row_count": result.row_count, "rows": result.rows, "error": result.error}
    return (
        f"{question}\n\n"
        "=== DATA (queried from our database for this partnership; "
        "quote these figures, do not recompute or invent) ===\n"
        f"{json.dumps(payload, ensure_ascii=False)}\n"
    )


def _planner_question(tasks: list[PlanTask], name: str) -> str | None:
    for task in tasks:
        if task.name != name:
            continue
        raw = task.input.get("question") or task.input.get("text") or ""
        if isinstance(raw, str) and raw.strip():
            return raw.strip()[:2000]
    return None


def _run_report(
    db: Database, llm: LLM, session: dict[str, Any], text: str, tasks: list[PlanTask], *,
    partnership_id: str, brand_id: str, distributor_id: str,
) -> Iterator[dict[str, Any]]:
    """One draft. The report package queries; this function only streams and saves it."""
    draft = draft_report(
        db, llm, message=text, history=list(session.get("history") or []),
        partnership_id=partnership_id, brand_id=brand_id, distributor_id=distributor_id,
        session_id=session["id"],
        hint=_planner_question(tasks, "data") or _planner_question(tasks, "report"),
    )
    for warning in draft.warnings:
        yield {"type": "warning", "message": warning}
    reply = draft.text
    yield {"type": "token", "text": reply}
    extra: dict[str, Any] = {"report_draft": True}
    if draft.charts:
        extra["charts"] = draft.charts
    if draft.web_notes:
        extra["web_notes"] = draft.web_notes
    if draft.data_query:
        extra["data_query"] = draft.data_query
    yield _save_reply(db, session, text, reply, extra)


def handle_message(
    db: Database, llm: LLM, session_id: str, text: str, images: list[str] | None = None, attachments: list[str] | None = None,
    documents: list[tuple[str, bytes]] | None = None,
) -> Iterator[dict[str, Any]]:
    session = db.session(session_id)
    if not session:
        raise KeyError("session not found")
    text = (text or "").strip()
    images = [u for u in (images or []) if isinstance(u, str) and u.startswith("data:image/")][:MAX_IMAGES]
    documents = [(n, b) for n, b in (documents or []) if n and b]
    if not text and not images and not documents:
        raise ValueError("message required")
    bundle = load_bundle(db, session["partnership_id"])
    names = [*(attachments or []), *[n for n, _ in documents], *[f"image-{i + 1}.jpg" for i in range(len(images))]]
    history = session.get("history") or []

    if not session.get("title"):
        opened = open_session(llm, message=text, attachments=names)
        db.update_session(session_id, title=opened.title, tags=opened.tags)
        session = db.session(session_id) or session
        yield {"type": "session", "title": opened.title, "tags": opened.tags}

    tags = list(session.get("tags") or [])
    tasks = make_plan(
        llm, message=text, attachments=names, history=history,
        stage=session.get("stage"), tags=tags,
    )
    yield {
        "type": "plan",
        "tasks": [{"id": t.id, "kind": t.kind, "name": t.name, "depends_on": t.depends_on} for t in tasks],
    }

    agents = _agent_names(tasks)
    if session.get("specialist") == "po" and session.get("stage") in ("gathering", "confirm"):
        agents = set(agents) | {"po"}

    if "po" in agents:
        if session.get("stage") == "submitted" and session.get("po_id"):
            po = db.po(session["po_id"])
            if po:
                outcome = handle_post_submit(db, llm, po, text)
                if outcome.kind != "none":
                    yield from _one_shot(db, session, outcome.message, {"po_event": outcome.kind})
                    return
                if outcome.message:
                    yield from _one_shot(db, session, outcome.message)
                    return
            db.update_session(session_id, slots={}, stage="gathering", po_id=None, confirm_token=None, confirm_hash=None)
            session = db.session(session_id) or session
        db.update_session(session_id, specialist="po")
        yield from run_po_turn(db, llm, bundle, session, text, images)
        return

    if "contract" in agents:
        db.update_session(session_id, specialist="contract")
        filed = file_contract(db, llm, session, documents)
        yield {
            "type": "contract",
            "status": filed["status"],
            "contract_id": filed["contract_id"],
            "table": filed["table"],
        }
        yield from _answer_directly(
            db, llm, session, text,
            prompt_text=_contract_prompt(text, filed),
            extra={"contract_id": filed["contract_id"], "contract": filed["table"]},
        )
        return

    if "report" in agents:
        # The draft queries for itself. A co-scheduled data task is only a hint.
        db.update_session(session_id, specialist="report")
        yield from _run_report(
            db, llm, session, text, tasks,
            partnership_id=bundle.partnership.id, brand_id=bundle.partnership.brand_id,
            distributor_id=bundle.partnership.distributor_id,
        )
        return

    if "data" in agents:
        db.update_session(session_id, specialist="data")
        result = answer_question(
            db, llm, question=_task_question(tasks, "data", text),
            partnership_id=bundle.partnership.id, brand_id=bundle.partnership.brand_id,
            distributor_id=bundle.partnership.distributor_id, session_id=session_id,
        )
        yield {"type": "data", "ok": result.ok, "row_count": result.row_count, "log_id": result.log_id}
        yield from _answer_directly(
            db, llm, session, text,
            prompt_text=_data_prompt(text, result),
            system=DATA_ANSWER,
            extra={"data_query": _query_mark(result)},
        )
        return

    if "brand" in agents:
        # Retrieval may use a standalone rewrite of a follow-up. The saved turn and the
        # answer prompt keep the user's words. Pages are already text.
        db.update_session(session_id, specialist="brand")
        query = _search_query(llm, text, history)
        try:
            hits = retrieve(query, bundle.partnership.brand_id)
        except RagUnavailable as err:
            yield from _one_shot(db, session, str(err))
            return
        named = _with_sources(db, hits)
        yield from _answer_directly(
            db, llm, session, text,
            prompt_text=evidence_prompt(text, named),
            system=BRAND_ANSWER,
            extra={"brand_hits": [
                {
                    "n": h["n"], "cid": h.get("cid"), "doc": h.get("doc"), "page": h.get("page"),
                    "file_name": h.get("file_name"), "source": h["source"],
                }
                for h in named
            ]},
        )
        return

    # No write agent: native tools + MCP via tools= / tool_choice=auto.
    tool_reply = run_tool_calls(
        llm, text=text, history=history, extra={"last_report": session.get("last_report")},
    )
    if tool_reply is not None:
        yield {"type": "tools", "used": True}
        yield from _one_shot(db, session, tool_reply)
        return

    yield from _answer_directly(db, llm, session, text)
