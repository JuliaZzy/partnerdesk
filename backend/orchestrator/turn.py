"""聊天入口：新会话的标题在 worker 里取，然后把这一轮交给工具循环。

会话取名只写聊天列表上的标题和标签。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from dataclasses import dataclass
from typing import Any

from ..db import Database
from ..llm import calls as model_calls
from ..llm.types import AssistantTurn
from ..memory import attach, prompt_turns
from ..services.chat_run import enqueue_chat_run, wake_chat_worker
from ..services.run_names import StepType, step_name
from ..tools import all_schemas, all_tools
from .prompt import OPEN, REACT

_HANDOFF = {"po": "采购单", "contract": "合同", "report": "报表", "data": "查数", "brand": "品牌资料"}
log = logging.getLogger(__name__)
MAX_IMAGES = 5
MAX_TOOL_ROUNDS = 4

TOPIC_KEYS = ("po", "contract", "report", "brand", "data")


@dataclass(frozen=True)
class SessionOpen:
    title: str
    tags: list[str]

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

def open_session(llm: Any, *, message: str, attachments: list[str], 
    db: Database | None = None, run_id: str | None = None
) -> SessionOpen:
    """First-turn only: tags + title. No history — the session does not have one yet."""
    user = (
        f"=== 附件 ===\n{', '.join(attachments) or '（无）'}\n\n"
        f"=== 消息 ===\n{message[:4000] or '（无文字）'}"
    )
    messages = [{"role": "user", "content": user}]
    request = {
        "model": model_calls.SCHOOL_CHAT,
        "system": OPEN,
        "messages": messages,
        "schema": session_open_schema(),
    }
    step_id = None 
    if db is not None and run_id:
        step = db.insert_step(run_id, StepType.MODEL, step_name(StepType.MODEL, None), {})
        step_id = None if step is None else step["id"]
    raw: dict[str, Any] = {}
    caught: Exception | None = None
    started = time.perf_counter()
    try:
        parsed = model_calls.school_json(
            model_calls.SCHOOL_CHAT, system=OPEN, messages=messages, 
            schema=session_open_schema(), temperature=0, client = llm
        )
        if isinstance(parsed, dict):
            raw = parsed
        else:
            caught = ValueError("Campus model did not return a JSON object")
    except Exception as err:
        caught = err
    
    if db is not None and run_id and step_id:
        usage = getattr(llm, "last_usage", {})
        usage = usage if isinstance(usage, dict) else {}
        db.insert_model_call(
            run_id, step_id, 
            provider=str(getattr(llm, "provider", None) or "sjtu"),
            model=model_calls.SCHOOL_CHAT,
            request=request,
            response={"json": raw} if raw else {},
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            latency_ms=int((time.perf_counter() - started) * 1000),
            status="failed" if caught is not None else "succeeded",
            error={"message": str(caught) if caught is not None else None},
        )
        db.finish_step(
            step_id, 
            "failed" if caught is not None else "succeeded",
            None if caught is not None else {"title": str(raw.get("title") or "")}, 
            {"message": str(caught)} if caught is not None else None,
        )
    if caught is not None:
        raise caught
    allowed = set(TOPIC_KEYS)
    tags = [t for t in (raw.get("tags") or []) if isinstance(t, str) and t in allowed]
    title = str(raw.get("title") or "").strip() or (attachments[0] if attachments else "New chat")
    return SessionOpen(title=title[:80], tags=tags)

def name_new_chat(db: Database, llm:Any, session: dict[str, Any], run: dict[str, Any]) -> None:
    """Name a chat that has no title yet. A model failure leaves the title empty."""
    payload = run.get("input_json") if isinstance(run.get("input_json"), dict) else {}
    message = str(payload.get("message") or "")
    attachments = [name for name in (payload.get("attachments") or []) if isinstance(name, str)]
    try:
        opened = open_session(
            llm, message=message, attachments=attachments, db=db, run_id=str(run["id"])
        )
    except Exception:
        log.exception("Failed to name new chat")
        return 
    db.update_session(session["id"], title=opened.title, tags=opened.tags)

def _run_one_tool(
    call: Any, runners: dict[str, Any], llm: Any, context: dict[str, Any],
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
    llm: Any, *, text: str, history: list[dict[str, Any]], extra: dict[str, Any] | None = None,
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
        last = model_calls.flash_tools(
            system=attach(REACT, memory or ""), messages=messages, tools=schemas,
        )
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
            # Workers outlive the call. Run them in this turn's context.
            parent = copy_context()
            with ThreadPoolExecutor(max_workers=min(5, len(calls))) as pool:
                futs = {pool.submit(parent.run, _run_one_tool, c, runners, llm, context): c.id for c in calls}
                for fut in as_completed(futs):
                    msg = fut.result()
                    by_id[msg["tool_call_id"]] = msg
            # Preserve the model's call order when appending (API is happier that way).
            messages.extend(by_id[c.id] for c in calls)
    return (last.content if last else None) or "Done."

def handle_message(
    db: Database, llm: Any, session_id: str, text: str, images: list[str] | None = None, attachments: list[str] | None = None,
    documents: list[tuple[str, bytes]] | None = None, model: str | None = None,
) -> Iterator[dict[str, Any]]:
    session = db.session(session_id)
    if not session:
        raise KeyError("session not found")
    text = (text or "").strip()
    images = [u for u in (images or []) if isinstance(u, str) and u.startswith("data:image/")][:MAX_IMAGES]
    documents = [(n, b) for n, b in (documents or []) if n and b]
    if not text and not images and not documents:
        raise ValueError("message required")
    names = [*(attachments or []), *[n for n, _ in documents], *[f"image-{i + 1}.jpg" for i in range(len(images))]]

    run = enqueue_chat_run(
        db, session, text, images=images, documents=documents, model=model, attachments=names
    )
    yield {"type": "run", "run_id": run["id"]}
    wake_chat_worker(run["id"])