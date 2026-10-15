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

from .claims import handle_post_submit
from .contract_read import read_and_ingest, summarize
from .db import Database
from .documents import SUPPORTED_EXTS
from .llm import LLM, AssistantTurn
from .po.turn import load_bundle, run_po_turn
from .prompts.orchestrator import ANSWER, PLAN, REACT
from .prompts.session_open import OPEN
from .tools import all_schemas, all_tools

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


def _answer_directly(db: Database, llm: LLM, session: dict[str, Any], text: str) -> Iterator[dict[str, Any]]:
    history = list(session.get("history") or [])
    messages = [*history[-8:], {"role": "user", "content": text}]
    parts: list[str] = []
    for chunk in llm.stream_text(system=ANSWER, messages=messages):
        parts.append(chunk)
        yield {"type": "token", "text": chunk}
    reply = "".join(parts).strip() or "How can I help?"
    new_history = [*history, {"role": "user", "content": text}, {"role": "assistant", "content": reply}]
    db.update_session(session["id"], history=new_history[-16:])
    yield {
        "type": "done", "stage": session.get("stage"), "slots": session.get("slots"), "draft": None,
        "gaps": [], "gaps_detail": [], "advisories": [], "steps": [], "confirm_token": None, "submitted": None,
    }


def _file_agreement(db: Database, llm: LLM, session: dict[str, Any], documents: list[tuple[str, bytes]], text: str) -> Iterator[dict[str, Any]]:
    readable = [(n, b) for n, b in documents if n.lower().endswith(SUPPORTED_EXTS)]
    if not readable:
        yield from _answer_directly(
            db, llm, session,
            text or "I want to file a distribution agreement but have not attached the file yet.",
        )
        return
    name, data = readable[0]
    try:
        rec = read_and_ingest(
            db, llm, brand_id=_brand_id(db, session["partnership_id"]), partnership_id=session["partnership_id"],
            file_name=name, data=data,
        )
    except Exception as err:
        yield from _one_shot(db, session, f"I couldn't file that agreement: {err}")
        return
    reply = summarize(rec)
    if len(readable) > 1:
        reply += f" I filed {name} and left the other {len(readable) - 1} file(s) unread."
    yield from _one_shot(db, session, reply, {"contract_id": rec.contract.id})


def _brand_id(db: Database, partnership_id: str) -> str:
    p = db.partnership(partnership_id)
    if not p:
        raise KeyError("partnership not found")
    return p.brand_id


def _agent_names(tasks: list[PlanTask]) -> set[str]:
    return {t.name for t in tasks if t.kind == "agent"}


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
        yield from _file_agreement(db, llm, session, documents, text)
        return

    if "report" in agents:
        db.update_session(session_id, specialist="report")
        yield from _answer_directly(db, llm, session, text)
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
