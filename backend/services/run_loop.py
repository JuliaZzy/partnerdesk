"""One harness pass over a run the worker has already claimed.

The checkpoint is the memory. `last` says which side of a tool call that memory
stopped on. A model iteration and a tool iteration each become one `agent_steps`
row after that side has been stored. `open_step_id` is set only while a tool
step is waiting for the user; it is not a step name.

The worker claims a lease, then calls `execute_run`. Creating through the
outbox and claiming that same job in this process is `run_in_this_process`.
Both enter `run_loop`.
"""

from __future__ import annotations

import inspect
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..db import Database, utcnow
from ..llm.types import AssistantTurn
from ..repositories.run_repository import (
    RunStatus,
    append_checkpoint,
    append_run_event,
    claim_tool_call,
    complete_tool_call,
    create_agent_run,
    find_agent_run_by_id,
    finish_step,
    insert_model_call,
    insert_step,
    insert_tool_call,
    latest_checkpoint,
    list_run_events,
    mark_waiting_for_user,
)
from .run_names import (
    COMPLETED_TOOL_CALLS_KEY,
    CONTEXT_KEY,
    LAST_INPUT,
    LAST_KEY,
    LAST_REPLY,
    StepEvent,
    StepStatus,
    StepType,
    checkpoint_last_after_tool,
    checkpoint_last_before_tool,
    step_name,
)
from .run_worker import LeaseLost, PermanentRunError

MAX_LOOP_STEPS = 8
_TOOL_USE = "assistant:tool_use:"
Memory = tuple[list[dict[str, Any]], list[str], str, str | None, str | None]

@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    fn: Callable[[dict[str, Any]], dict[str, Any]]
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    requires_approval: bool = False

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            step_name(StepType.TOOL, tool.name)
            if tool.name in self._tools:
                raise ValueError(f"Tool {tool.name} already registered")
            self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        tool = self._tools.get(name)
        if tool is None:
            raise ValueError(f"Tool {name} not found")
        return tool

    def tools(self) -> list[Tool]:
        return list(self._tools.values())

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

def _caller(fn: Any) -> tuple[str, str]:
    return str(getattr(fn, "provider", "fake")), str(getattr(fn, "model", "fake"))

def execute_run(
    db: Database,
    tools: ToolRegistry,
    *,
    decide: Callable[[list[dict[str, Any]], list[dict[str, Any]]], AssistantTurn],
    answer: Callable[[list[dict[str, Any]]], str],
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """The callable `work_once` takes. The database and the model stay in the closure."""
    def execute(run: dict[str, Any]) -> dict[str, Any]:
        return run_loop(db, run, tools, decide, answer)
    return execute

def run_in_this_process(
    db: Database,
    org_id: str,
    user_id: str,
    run_type: str,
    idempotency_key: str,
    run_input: dict[str, Any],
    tools: ToolRegistry,
    decide: Callable[[list[dict[str, Any]], list[dict[str, Any]]], AssistantTurn],
    answer: Callable[[list[dict[str, Any]]], str],
    *,
    parent_run_id: str | None = None,
    worker_id: str | None = None,
) -> dict[str, Any]:
    """Create through the outbox, then claim this job and start the run here."""
    from .dispatcher import dispatch_pending
    from .run_worker import work_once

    owner = worker_id or user_id
    run, _created = create_agent_run(db, org_id, user_id, run_type, idempotency_key, run_input, parent_run_id)
    dispatch_pending(db)
    work_once(db, owner, execute_run(db, tools, decide=decide, answer=answer), run_id=run["id"])
    row = find_agent_run_by_id(db, run["id"])
    if row is None:
        raise RuntimeError("run disappeared before it started")
    return row

def run_loop(
    db: Database, 
    run: dict[str, Any], 
    tools: ToolRegistry, 
    decide: Callable[[list[dict[str, Any]], list[dict[str, Any]]], AssistantTurn], 
    answer: Callable[[list[dict[str, Any]]], str]
) -> dict[str, Any]:
    owner = run.get("lease_owner")
    if not isinstance(owner, str) or not owner:
        raise LeaseLost("claimed run has no lease owner")
    decide_provider, decide_model_name = _caller(decide)
    answer_provider, answer_model_name = _caller(answer)
    loaded = _load(db, run)
    if loaded is None or _stopped(db, run["id"], owner):
        return{}
    context, completed, last, open_step_id, resume_note = loaded
    if last == LAST_REPLY:
        return {"text": _assistant_text(context)}
    for _ in range(MAX_LOOP_STEPS):
        if _stopped(db, run["id"], owner):
            return {}
        if last.startswith(_TOOL_USE):
            moved = _finish_tool(
                db, run, tools, context, completed, last[len(_TOOL_USE):], open_step_id, resume_note,
                None, 0, decide_provider, decide_model_name,
            )
            if moved is None:
                return {}
            context, completed, last, open_step_id, resume_note = moved
            continue
        started = time.perf_counter()
        _event(db, run, StepEvent.MODEL_STARTED, {})
        turn = _decide(decide, context, tools)
        latency_ms = int((time.perf_counter() - started) * 1000)
        if turn.tool_calls:
            call = turn.tool_calls[0]
            tool = tools.get(call.name)
            context = [*context, _assistant_message(turn)]
            if tool.requires_approval:
                if _pause(db, run, context, completed, call, turn, latency_ms, decide_provider, decide_model_name) is None:
                    return {}
                return {}
            if _save(db, run["id"], context, completed, checkpoint_last_before_tool(call.name)) is None:
                return {}
            moved = _finish_tool(
                db, run, tools, context, completed, call.name, None, resume_note, turn, latency_ms,
                decide_provider, decide_model_name
            )
            if moved is None:
                return {}
            context, completed, last, open_step_id, resume_note = moved
            continue 
        text = (turn.content or "").strip()
        reply_provider, reply_model_name, reply_ms = decide_provider, decide_model_name, latency_ms
        if not text:
            started = time.perf_counter()
            text = answer(context).strip()
            reply_ms = int((time.perf_counter() - started) * 1000)
            reply_provider, reply_model_name = answer_provider, answer_model_name
            turn = AssistantTurn(
                content=text,
                prompt_tokens=int(getattr(answer, "prompt_tokens", 0) or 0),
                completion_tokens=int(getattr(answer, "completion_tokens", 0) or 0),
            )
        if not text:
            raise PermanentRunError("model returned empty content")
        context = [*context, _assistant_message(turn)]
        if _save(db, run["id"], context, completed, LAST_REPLY) is None:
            return {}
        if _record_model_step(db, run, context, turn, reply_ms, reply_provider, reply_model_name, {"text": text}) is None:
            return {}
        return {"text": text}
    raise PermanentRunError("max loop steps exceeded")

def _load(db: Database, run: dict[str, Any]) -> Memory | None:
    row = latest_checkpoint(db, run["id"])
    if row is None:
        context = [{"role": "user", "content": _user_text(run.get("input_json") or {})}]
        if _save(db, run["id"], context, [], LAST_INPUT, last_report=_input_report(run)) is None:
            return None 
        return context, [], LAST_INPUT, None, None
    state = row["state_json"]
    context = list(state[CONTEXT_KEY])
    completed = [item for item in state[COMPLETED_TOOL_CALLS_KEY] if isinstance(item, str)]
    last = state[LAST_KEY]
    open_step_id = state.get("open_step_id") if isinstance(state.get("open_step_id"), str) else None
    note = _resume_note(db, run, row["created_at"])
    if note and last.startswith(_TOOL_USE):
        return context, completed, last, open_step_id, note
    if note and last != LAST_REPLY:
        context = [*context, {"role": "user", "content": note}]
        if _save(db, run["id"], context, completed, LAST_INPUT) is None:
            return None
        return context, completed, LAST_INPUT, None, None
    return context, completed, last, open_step_id, None

def _invoke_tool(
    db: Database,
    run_id: str,
    tool: Tool,
    args: dict[str, Any],
    context: list[dict[str, Any]],
    step_id: str | None = None,
) -> Any:
    payload = dict(args)
    if _accepts_context(tool.fn):
        return tool.fn(
            payload, 
            context=_tool_context(context, _checkpoint_report(db, run_id), run_id=run_id, step_id=step_id)
        )
    return tool.fn(payload)

def _accepts_context(fn: Callable[[dict[str, Any], dict[str, Any]], Any]) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    if "context" in params:
        return True
    return any(param.kind is inspect.Parameter.VAR_KEYWORD for param in params.values())

def _tool_context(
    context: list[dict[str, Any]], 
    last_report: str = "",
    *, 
    run_id: str | None = None,
    step_id: str | None = None,
) -> dict[str, Any]:
    history: list[dict[str, Any]] = []
    for message in context:
        if not isinstance(message, dict) or message.get("role") not in ("assistant", "user"):
            continue 
        text = _plain_text(message.get("content"))
        if text:
            history.append({"role": str(message["role"]), "content": text})
    payload: dict[str, Any] = {"history": history, "run_id": run_id}
    if step_id:
        payload["step_id"] = step_id
    if last_report:
        payload["last_report"] = last_report
    return payload

def _plain_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = [
        str(block.get("text") or "").strip()
        for block in content
        if isinstance(block, dict) and block.get("type") == "text" and str(block.get("text") or "").strip()
    ]
    return "\n".join(parts)

def _finish_tool(
    db: Database,
    run: dict[str, Any],
    tools: ToolRegistry,
    context: list[dict[str, Any]],
    completed: list[str],
    tool_name: str,
    open_step_id: str | None,
    resume_note: str | None,
    turn: AssistantTurn | None,
    latency_ms: int,
    provider: str,
    model_name: str,
) -> Memory | None:
    # 找模型要调用的工具块是否存在
    block = _last_tool_use(context)
    if block is None or block.get("name") != tool_name:
        raise PermanentRunError("checkpoint tool call does not match the context")
    tool_use_id = block.get("id")
    if not isinstance(tool_use_id, str) or tool_use_id in completed:
        raise PermanentRunError("tool call id is missing or already completed")
    tool = tools.get(tool_name)
    args = block.get("input") if isinstance(block.get("input"), dict) else {}

    # 检查是否被其他 worker 抢占了
    if _stopped(db, run["id"], str(run.get("lease_owner") or "")):
        return None

    # 提前算好幂等键
    key = f"{tool_name}:{tool_use_id}"
    step_id = open_step_id     # 记录未完成的步骤

    # 需要审批的工具，跳过只记结果，不调用函数
    skipped = tool.requires_approval and _resume_skipped(db, run)
    if skipped:
        if step_id is None:
            return None 
        result = {"skipped": True}
        tool_ms = 0
        if insert_tool_call(
            db, run["id"], step_id,
            tool_name=tool_name, args=args, result=result, status=StepStatus.SUCCEEDED,
            idempotency_key=key, latency_ms=tool_ms,
        ) is None:
            return None
    elif tool.requires_approval:
        if step_id is None:
            step_id = _record_model_step(
                db, run, context, turn, latency_ms, provider, model_name, None, tool_name, args
            )
        if step_id is None:
            return None
        # 抢占工具调用
        claimed = claim_tool_call(
            db, run["id"], step_id, tool_name=tool_name, args=args, idempotency_key=key
        )
        if claimed is None:
            return None
        row, created = claimed 
        # 如果幂等键已存在，则直接使用已有的结果
        if not created:
            if row.get("status") != StepStatus.SUCCEEDED:
                raise PermanentRunError("tool call already completed")
            stored = row.get("result_json")
            result = stored if isinstance(stored, dict) else {}
            tool_ms = int(row.get("latency_ms") or 0)
        # 抢座成功，执行
        else:
            _event(db, run, StepEvent.TOOL_STARTED, {"tool_name": tool_name, "tool_use_id": tool_use_id})
            started = time.perf_counter()
            try:
                result = _invoke_tool(db, run["id"], tool, args, context, step_id)
            except Exception as e:
                complete_tool_call(
                    db, row["id"], result={}, status=StepStatus.FAILED, latency_ms=0, 
                    error={"message": str(e)}
                )    # 异常时先标记为 failed，再抛出永久错误
                raise PermanentRunError(str(e)) from e 
            # 记录工具调用时间
            tool_ms = int((time.perf_counter() - started) * 1000)
            # 工具返回的不是字典，则标记为 failed
            if not isinstance(result, dict):
                complete_tool_call(
                    db, row["id"], result={}, status=StepStatus.FAILED, latency_ms=tool_ms,
                    error={"message": "tool returned a non-object"}
                )
                raise PermanentRunError("tool returned a non-object")
            if complete_tool_call(
                db, row["id"], result=result, status=StepStatus.SUCCEEDED, latency_ms=tool_ms,
            ) is None:
                return None
    # 不需要审批的工具
    else:
        if step_id is None:
            step_id = _record_model_step(
                db, run, context, turn, latency_ms, provider, model_name, None, tool_name, args
            )
        if step_id is None:
            return None
        _event(db, run, StepEvent.TOOL_STARTED, {"tool_name": tool_name, "tool_use_id": tool_use_id})
        started = time.perf_counter()
        result = _invoke_tool(db, run["id"], tool, args, context, step_id)
        tool_ms = int((time.perf_counter() - started) * 1000)
        if not isinstance(result, dict):
            raise PermanentRunError("tool returned a non-object")
    context = [*context, _tool_result_message(tool_use_id, result)]
    completed = [*completed, tool_use_id]
    if _save(db, run["id"], context, completed, checkpoint_last_after_tool(tool_name)) is None:
        return None
    if not tool.requires_approval:
        saved = insert_tool_call(
            db, run["id"], step_id,
            tool_name=tool_name, args=args, result=result, status=StepStatus.SUCCEEDED,
            idempotency_key=f"{tool_name}:{tool_use_id}", latency_ms=tool_ms,
        )
        if saved is None:
            return None
    if finish_step(db, step_id, StepStatus.SUCCEEDED, result) is None:
        return None 
    
    # 用户没有留言 -> 返回“工具已跑完”的状态，插入消息历史中， 存 LAST_INPUT 后返回
    if not resume_note:
        return context, completed, checkpoint_last_after_tool(tool_name), None, None
    context = [*context, {"role": "user", "content": resume_note}]
    if _save(db, run["id"], context, completed, LAST_INPUT) is None:
        return None
    return context, completed, LAST_INPUT, None, None

def _pause(
    db: Database,
    run: dict[str, Any],
    context: list[dict[str, Any]],
    completed: list[str],
    call: Any,
    turn: AssistantTurn,
    latency_ms: int,
    provider: str,
    model_name: str,
) -> str | None:
    args = call.arguments if isinstance(call.arguments, dict) else {}
    step_id = _record_model_step(db, run, context, turn, latency_ms, provider, model_name, None, call.name, args)
    if step_id is None:
        return None
    if _save(db, run["id"], context, completed, checkpoint_last_before_tool(call.name), step_id) is None:
        return None
    parked = mark_waiting_for_user(db, run["id"], {"tool_name": call.name, "tool_use_id": call.id})
    return step_id if parked is not None else None

def _record_model_step(
    db: Database,
    run: dict[str, Any],
    context: list[dict[str, Any]],
    turn: AssistantTurn | None,
    latency_ms: int,
    provider: str,
    model_name: str,
    output: dict[str, Any] | None,
    tool_name: str | None = None,
    tool_args: dict[str, Any] | None = None,
) -> str | None:
    if tool_name is None:
        step_type, name, step_input = StepType.MODEL, step_name(StepType.MODEL, None), {}
    else:
        step_type, name, step_input = StepType.TOOL, step_name(StepType.TOOL, tool_name), tool_args or {}
    step = insert_step(db, run["id"], step_type, name, step_input)
    if step is None:
        return None
    response = _assistant_message(turn) if turn is not None else _last_assistant(context)
    prompt_tokens = int(getattr(turn, "prompt_tokens", 0) or 0) if turn is not None else 0
    completion_tokens = int(getattr(turn, "completion_tokens", 0) or 0) if turn is not None else 0
    recorded = insert_model_call(
        db, run["id"], step["id"],
        provider=provider, model=model_name, request={"messages": _request_messages(context)},
        response=response, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        latency_ms=latency_ms, status=StepStatus.SUCCEEDED,
    )
    if recorded is None:
        return None
    if output is not None and finish_step(db, step["id"], StepStatus.SUCCEEDED, output) is None:
        return None
    return step["id"]

def _decide(decide, context, tools) -> AssistantTurn:
    turn = decide(context, tools.schemas())
    if not isinstance(turn, AssistantTurn):
        raise PermanentRunError("orchestrator returned an unexpected value")
    return turn

def _stopped(db: Database, run_id: str, owner: str) -> bool:
    fresh = find_agent_run_by_id(db, run_id)
    if fresh is None or fresh["status"] != RunStatus.RUNNING:
        return True
    if fresh.get("cancel_requested_at") is not None:
        return True
    expires = fresh.get("lease_expires_at")
    if fresh.get("lease_owner") != owner or expires is None or expires <= utcnow():
        raise LeaseLost(f"lease lost for {run_id}")
    return False

def _save(
    db: Database,
    run_id: str,
    context: list[dict[str, Any]],
    completed: list[str],
    last: str,
    open_step_id: str | None = None,
    last_report: str | None = None,
) -> dict[str, Any] | None:
    state: dict[str, Any] = {CONTEXT_KEY: context, COMPLETED_TOOL_CALLS_KEY: completed, LAST_KEY: last}
    if open_step_id:
        state["open_step_id"] = open_step_id
    if last_report is None:
        previous = latest_checkpoint(db, run_id)
        saved = previous["state_json"].get("last_report") if previous else None
        last_report = saved if isinstance(saved, str) else ""
    if last_report.strip():
        state["last_report"] = last_report.strip()
    return append_checkpoint(db, run_id, state)

def _event(db: Database, run: dict[str, Any], event_type: StepEvent, payload: dict[str, Any]) -> None:
    append_run_event(db, run["org_id"], run["id"], event_type, payload)

def _resume_skipped(db: Database, run: dict[str, Any]) -> bool:
    """The latest resume decides. Anything other than skip still runs the tool."""
    events = list_run_events(db, run["org_id"], run["id"]) or []
    decision = None
    for event in events:
        if event["event_type"] != "run.resumed":
            continue
        incoming = event["payload_json"].get("input") if isinstance(event["payload_json"], dict) else None
        raw = incoming.get("decision") if isinstance(incoming, dict) else None
        if raw in ("skip", "confirm"):
            decision = raw
    return decision == "skip"

def _resume_note(db: Database, run: dict[str, Any], checkpoint_at: Any) -> str | None:
    events = list_run_events(db, run["org_id"], run["id"]) or []
    note = None
    for event in events:
        if event["event_type"] != "run.resumed" or event["created_at"] <= checkpoint_at:
            continue
        incoming = event["payload_json"].get("input") if isinstance(event["payload_json"], dict) else None
        message = incoming.get("message") if isinstance(incoming, dict) else None
        if isinstance(message, str) and message.strip():
            note = message
    return note

def _input_report(run: dict[str, Any]) -> str:
    """The report body supplied with this run. Later checkpoints copy it forward."""
    raw = run.get("input_json")
    report = raw.get("last_report") if isinstance(raw, dict) else None
    return report if isinstance(report, str) else ""

def _checkpoint_report(db: Database, run_id: str) -> str:
    row = latest_checkpoint(db, run_id)
    if row is None:
        return ""
    stored = row["state_json"].get("last_report")
    return stored.strip() if isinstance(stored, str) else ""

def _user_text(run_input: dict[str, Any]) -> str:
    message = run_input.get("message") if isinstance(run_input, dict) else None
    if isinstance(message, str) and message.strip():
        return message
    return json.dumps(run_input, ensure_ascii=False)

def _assistant_message(turn: AssistantTurn) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    if turn.content:
        blocks.append({"type": "text", "text": turn.content})
    for call in turn.tool_calls:
        blocks.append({"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments})
    return {"role": "assistant", "content": blocks}

def _tool_result_message(tool_use_id: str, result: dict[str, Any]) -> dict[str, Any]:
    return {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": result}]}

def _last_tool_use(context: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not context:
        return None
    content = context[-1].get("content")
    if not isinstance(content, list):
        return None
    found = [block for block in content if isinstance(block, dict) and block.get("type") == "tool_use"]
    return found[-1] if found else None

def _last_assistant(context: list[dict[str, Any]]) -> dict[str, Any]:
    for message in reversed(context):
        if message.get("role") == "assistant":
            return message
    return {"role": "assistant", "content": []}

def _request_messages(context: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for index in range(len(context) - 1, -1, -1):
        if context[index].get("role") == "assistant":
            return context[:index]
    return context

def _assistant_text(context: list[dict[str, Any]]) -> str:
    content = _last_assistant(context).get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text")
