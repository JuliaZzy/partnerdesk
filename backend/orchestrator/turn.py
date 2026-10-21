"""Chat entry: open the session, ask for a plan, then call one agent by name.

Tools and MCP stay here. They are not agents: the model picks them with `tools=`.
Brand retrieval is not an agent either; the plan name `brand` calls that function.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from dataclasses import dataclass, field
from typing import Any

from ..brand_knowledge.answer import answer_in_chat
from ..contract.agent import run as run_contract
from ..db import Database
from ..llm import LLM, AssistantTurn
from ..memory import attach, prompt_context, prompt_turns, standing_block, summary_block
from ..po.agent import run as run_po
from ..po.turn import load_bundle
from ..report.agent import run as run_report
from ..report.turn import asks_only_for_charts, has_report
from ..text2sql.agent import run as run_data
from ..tools import all_schemas, all_tools
from .prompt import OPEN, PLAN, REACT
from .speak import answer_directly, one_shot
from .status import begin_activity, status

_HANDOFF = {"po": "采购单", "contract": "合同", "report": "报表", "data": "查数", "brand": "品牌资料"}

MAX_IMAGES = 3
MAX_TOOL_ROUNDS = 4

TOPIC_KEYS = ("po", "contract", "report", "brand", "data")


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
    stage: str | None, tags: list[str], memory: str | None = None, folded_through: int = 0,
) -> list[PlanTask]:
    recent = "\n".join(
        f"{m['role']}: {str(m['content'])[:500]}" for m in prompt_turns(history, folded_through)
    )
    user = (
        f"=== SESSION TAGS ===\n{', '.join(tags) or '(none)'}\n"
        f"=== STAGE ===\n{stage or '(none)'}\n\n"
        f"=== RECENT CONVERSATION ===\n{recent or '(none)'}\n\n"
        f"=== ATTACHMENTS ===\n{', '.join(attachments) or '(none)'}\n\n"
        f"=== MESSAGE ===\n{message[:4000] or '(no text)'}"
    )
    raw = llm.complete_json(
        system=attach(PLAN, memory or ""), messages=[{"role": "user", "content": user}],
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
    include_mcp: bool | None = None, memory: str | None = None, folded_through: int = 0,
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
    messages: list[dict[str, Any]] = [
        {"role": m.get("role"), "content": str(m.get("content") or "")}
        for m in prompt_turns(history, folded_through)
        if m.get("role") in ("user", "assistant")
    ]
    messages.append({"role": "user", "content": text})
    context = {"history": history, **(extra or {})}
    used = False
    last: AssistantTurn | None = None
    for _ in range(MAX_TOOL_ROUNDS):
        last = llm.complete(system=attach(REACT, memory or ""), messages=messages, tools=schemas)
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
            # Workers outlive the call. Run in this turn's context so a tool's
            # JSON lands on this chat's step list, not whatever the thread did last.
            parent = copy_context()
            with ThreadPoolExecutor(max_workers=min(5, len(calls))) as pool:
                futs = {pool.submit(parent.run, _run_one_tool, c, runners, llm, context): c.id for c in calls}
                for fut in as_completed(futs):
                    msg = fut.result()
                    by_id[msg["tool_call_id"]] = msg
            # Preserve the model's call order when appending (API is happier that way).
            messages.extend(by_id[c.id] for c in calls)
    return (last.content if last else None) or "Done."


def _agent_names(tasks: list[PlanTask]) -> set[str]:
    return {t.name for t in tasks if t.kind == "agent"}


def _task_text(tasks: list[PlanTask], name: str) -> str | None:
    """Question the planner put on this task, when it put one."""
    for task in tasks:
        if task.name != name:
            continue
        raw = task.input.get("question") or task.input.get("text") or ""
        if isinstance(raw, str) and raw.strip():
            return raw.strip()[:2000]
    return None


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
    begin_activity()
    bundle = load_bundle(db, session["partnership_id"])
    names = [*(attachments or []), *[n for n, _ in documents], *[f"image-{i + 1}.jpg" for i in range(len(images))]]
    history = session.get("history") or []

    if not session.get("title"):
        # The title is for the chat list. It is not a step the distributor watches.
        opened = open_session(llm, message=text, attachments=names)
        db.update_session(session_id, title=opened.title, tags=opened.tags)
        session = db.session(session_id) or session
        yield {"type": "session", "title": opened.title, "tags": opened.tags}

    tags = list(session.get("tags") or [])
    folded = int(session.get("folded_through") or 0)
    remembered = prompt_context(standing_block(db, bundle.partnership.id), summary_block(session.get("summary")))
    partnership_id = bundle.partnership.id
    brand_id = bundle.partnership.brand_id
    distributor_id = bundle.partnership.distributor_id
    # A chart on the draft already written is not a new report. Deciding that here
    # keeps the planner from scheduling split, query, news, and a rewrite.
    if asks_only_for_charts(text) and has_report(history):
        yield status("plan", "Planning", "只补图表", state="done")
        yield {"type": "plan", "tasks": [{"id": "charts", "kind": "agent", "name": "report", "depends_on": []}]}
        yield from run_report(
            db, llm, session, text,
            partnership_id=partnership_id, brand_id=brand_id, distributor_id=distributor_id,
            hint=None, charts_only=True,
        )
        return

    yield status("plan", "Planning", "LLM is planning the next step", state="running")
    tasks = make_plan(
        llm, message=text, attachments=names, history=history,
        stage=session.get("stage"), tags=tags, memory=remembered, folded_through=folded,
    )
    yield {
        "type": "plan",
        "tasks": [{"id": t.id, "kind": t.kind, "name": t.name, "depends_on": t.depends_on} for t in tasks],
    }
    chosen = next((name for name in _HANDOFF if name in _agent_names(tasks) or (
        name == "po" and session.get("specialist") == "po" and session.get("stage") in ("gathering", "confirm")
    )), None)

    agents = _agent_names(tasks)
    if session.get("specialist") == "po" and session.get("stage") in ("gathering", "confirm"):
        agents = set(agents) | {"po"}
        chosen = chosen or "po"
    yield status("plan", "Planning", _HANDOFF.get(chosen or "", "直接回答"), state="done")

    if "po" in agents:
        yield from run_po(db, llm, session, text, images, bundle)
        return
    if "contract" in agents:
        yield from run_contract(db, llm, session, text, documents)
        return
    if "report" in agents:
        # The draft queries for itself. A co-scheduled data task is only a hint.
        # charts_only is the planner's backup when the message did not match the chart check above.
        charts_only = has_report(history) and any(
            task.name == "report" and task.input.get("charts_only") is True for task in tasks
        )
        yield from run_report(
            db, llm, session, text,
            partnership_id=partnership_id, brand_id=brand_id, distributor_id=distributor_id,
            hint=_task_text(tasks, "data") or _task_text(tasks, "report"),
            charts_only=charts_only,
        )
        return
    if "data" in agents:
        yield from run_data(
            db, llm, session, text, _task_text(tasks, "data") or text,
            partnership_id=partnership_id, brand_id=brand_id, distributor_id=distributor_id,
        )
        return
    if "brand" in agents:
        yield from answer_in_chat(db, llm, session, text, history, brand_id)
        return

    yield status("tools", "工具", "正在看要不要查新闻、汇率或翻译", state="running")
    tool_reply = run_tool_calls(
        llm, text=text, history=history, extra={"last_report": session.get("last_report")},
        memory=remembered, folded_through=folded,
    )
    if tool_reply is not None:
        yield status("tools", "工具", "工具已返回", state="done")
        yield {"type": "tools", "used": True}
        yield from one_shot(db, session, tool_reply, llm=llm)
        return
    yield status("tools", "工具", "这一轮没有调用工具", state="done")
    yield status("answer", "回答", "正在直接回复", state="running")
    done = yield from answer_directly(db, llm, session, text)
    yield status("answer", "回答", "回复已写完", state="done")
    yield done
