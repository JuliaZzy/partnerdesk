"""Callbacks for the tool loop, not a replacement for the desk agents.

`decide` sends the tool schemas to the injected model and gets back a tool
call or some text. `answer` asks that same model for a final reply when
decide returned neither. The tools themselves are registered in `session_tools.tools`.

Purchase orders, contracts, reports, data queries, and code fixes keep their
own modules. This file does not price an order, read a contract, or write a
session.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from ..db import Database
from ..llm.client import DeepSeekClient, GLMClient
from ..llm.types import AssistantTurn, ToolCall
from ..memory import attach, prompt_context, standing_block, summary_block
from ..orchestrator.prompt import ANSWER, REACT
from ..repositories.run_repository import note_running_reply
from .run_loop import ToolRegistry, execute_run
from .session_tools import tools as turn_tools


def bind(
    llm: Any, *, decide_system: str = REACT, answer_system: str = ANSWER,
    on_text: Callable[[str], None] | None = None,
) -> tuple[Callable[..., AssistantTurn], Callable[..., str]]:
    """Turn one injected client into the harness callbacks.

    `llm` is a DeepSeek, Qwen, campus, or GLM client. Set `llm.model` to the call
    name. Token counts come from the `last_usage` that client stored. `on_text`
    receives the prose as it grows.
    """
    provider, model = _identity(llm)

    def decide(context: list[dict[str, Any]], schemas: list[dict[str, Any]]) -> AssistantTurn:
        resp = _invoke(llm, system=decide_system, messages=_tool_messages(context), tools=schemas, on_text=on_text)
        return _with_usage(_as_turn(llm, resp), _take_usage(llm))

    def answer(context: list[dict[str, Any]]) -> str:
        resp = _invoke(llm, system=answer_system, messages=_answer_messages(context), tools=None, on_text=on_text)
        text = _as_text(llm, resp)
        prompt_tokens, completion_tokens = _take_usage(llm)
        answer.prompt_tokens = prompt_tokens
        answer.completion_tokens = completion_tokens
        return text

    decide.provider, decide.model = provider, model
    answer.provider, answer.model = provider, model
    return decide, answer


def execute_for(
    db: Database,
    llm: Any,
    tools: ToolRegistry | None = None,
    *,
    session: dict[str, Any] | None = None,
    text: str = "",
    images: list[str] | None = None,
    documents:list[tuple[str, bytes]] | None = None,
    decide_system: str = REACT,
    answer_system: str = ANSWER,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """The callable `work_once` takes for the tool loop."""
    if session is not None:
        memory = prompt_context(
            standing_block(db, session["partnership_id"]),
            summary_block(session.get("summary")),
        )
        decide_system = attach(decide_system, memory)
        answer_system = attach(answer_system, memory)
    if tools is None:
        if session is None:
            raise ValueError("session is required when tools is None")
        tools = turn_tools(db, llm, session, text, images=images, documents=documents)

    def execute(run: dict[str, Any]) -> dict[str, Any]:
        def on_text(growing: str) -> None:
            note_running_reply(db, run["id"], growing)
            
        decide, answer = bind(llm, decide_system=decide_system, answer_system=answer_system, on_text=on_text)
        return execute_run(db, tools, decide=decide, answer=answer)(run)

    return execute

def _model_name(llm: Any) -> str:
    model = getattr(llm, "model", None) or getattr(getattr(llm, "settings", None), "model", None)
    if not model:
        raise ValueError("injected client has no model")
    return str(model)


def _invoke(
    llm: Any, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None,
    on_text: Callable[[str], None] | None = None,
) -> Any:
    """One non-streaming call. The client records `last_usage` before returning."""
    model = _model_name(llm)
    full = [{"role": "system", "content": system}, *messages]
    if isinstance(llm, GLMClient):
        kwargs: dict[str, Any] = {"model": model, "input": full}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        resp = llm.response(**kwargs)
        _publish(on_text, _as_text(llm, resp))
        return resp
    chat = getattr(llm, "chat_completion", None)
    if chat is None:
        raise TypeError(f"{type(llm).__name__} cannot answer a run")
    kwargs = {"model": model, "messages": full}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
        if isinstance(llm, DeepSeekClient):
            # Official tool calls in this desk turn thinking off. Campus thinking is another model name.
            kwargs["reasoning_effort"] = "none"
    if inspect.iscoroutinefunction(chat):
        return asyncio.run(chat(**kwargs))
        _publish(on_text, _as_text(llm, resp))
        return resp
    if on_text is None:
        return chat(**kwargs)
    kwargs["stream"] = True
    if isinstance(llm, DeepSeekClient):
        kwargs["stream_options"] = {"include_usage": True}
    return _assemble_stream(chat(**kwargs), on_text)

def _publish(on_text: Callable[[str], None] | None, text: str) -> None:
    if on_text is not None and text:
        on_text(text)

def _assemble_stream(chunks: Any, on_text: Callable[[str], None]) -> Any:
    parts: list[str] = []
    calls: dict[int, dict[str, str]] = {}
    for chunk in chunks:
        delta = _choice_delta(chunk)
        piece = _delta_text(delta)
        if piece:
            parts.append(piece)
            on_text("".join(parts))
        _take_tool_delta(calls, delta)
    message = SimpleNamespace(
        content="".join(parts) or None,
        tool_calls=[_tool_namespace(calls[index]) for index in sorted(calls)] or None,
    )
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])

def _choice_delta(chunk: Any) -> Any:
    if isinstance(chunk, dict):
        choices = chunk.get("choices") or []
        if not choices:
            return None
        return (choices[0] or {}).get("delta")
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return None
    return getattr(choices[0], "delta", None)

def _delta_text(delta: Any) -> str:
    if delta is None:
        return ""
    content = delta.get("content") if isinstance(delta, dict) else getattr(delta, "content", None)
    return content if isinstance(content, str) else ""

def _take_tool_delta(calls: dict[int, dict[str, str]], delta: Any) -> None:
    if delta is None:
        return
    raw = delta.get("tool_calls") if isinstance(delta, dict) else getattr(delta, "tool_calls", None)
    for call in raw or []:
        if isinstance(call, dict):
            index = int(call.get("index") or 0)
            slot = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
            if call.get("id"):
                slot["id"] = str(call["id"])
            function = call.get("function") or {}
            if function.get("name"):
                slot["name"] = str(function["name"])
            if function.get("arguments"):
                slot["arguments"] += str(function["arguments"])
            continue
        index = int(getattr(call, "index", 0) or 0)
        slot = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
        if getattr(call, "id", None):
            slot["id"] = str(call.id)
        function = getattr(call, "function", None)
        name = getattr(function, "name", None) if function is not None else None
        arguments = getattr(function, "arguments", None) if function is not None else None
        if name:
            slot["name"] = str(name)
        if arguments:
            slot["arguments"] += str(arguments)

def _tool_namespace(slot: dict[str, str]) -> SimpleNamespace:
    return SimpleNamespace(
        id=slot["id"],
        function=SimpleNamespace(
            name=slot["name"],
            arguments=slot["arguments"],
        ),
    )

def _as_turn(llm: Any, resp: Any) -> AssistantTurn:
    if isinstance(llm, GLMClient):
        return _turn_from_responses(resp)
    return _turn_from_chat(resp)

def _as_text(llm: Any, resp: Any) -> str:
    if isinstance(llm, GLMClient):
        return str(getattr(resp, "output_text", None) or "").strip()
    text = _message_text(_chat_message(resp))
    return text.strip() if text else ""


def _turn_from_chat(resp: Any) -> AssistantTurn:
    message = _chat_message(resp)
    calls = [_one_call(call) for call in _tool_calls(message)]
    return AssistantTurn(content=_message_text(message), tool_calls=calls)


def _turn_from_responses(resp: Any) -> AssistantTurn:
    calls: list[ToolCall] = []
    for item in getattr(resp, "output", None) or []:
        if getattr(item, "type", None) != "function_call":
            continue
        calls.append(ToolCall(
            id=str(getattr(item, "call_id", None) or getattr(item, "id", None) or ""),
            name=str(getattr(item, "name", None) or ""),
            arguments=_arguments(getattr(item, "arguments", None)),
        ))
    text = getattr(resp, "output_text", None)
    return AssistantTurn(content=str(text) if text else None, tool_calls=calls)


def _chat_message(resp: Any) -> Any:
    if isinstance(resp, dict):
        return resp["choices"][0]["message"]
    return resp.choices[0].message


def _message_text(message: Any) -> str | None:
    text = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
    if text is None:
        return None
    text = str(text)
    return text or None


def _tool_calls(message: Any) -> list[Any]:
    raw = message.get("tool_calls") if isinstance(message, dict) else getattr(message, "tool_calls", None)
    return list(raw or [])


def _one_call(call: Any) -> ToolCall:
    if isinstance(call, dict):
        function = call.get("function") or {}
        return ToolCall(
            id=str(call.get("id") or ""),
            name=str(function.get("name") or ""),
            arguments=_arguments(function.get("arguments")),
        )
    return ToolCall(
        id=str(call.id),
        name=str(call.function.name),
        arguments=_arguments(call.function.arguments),
    )


def _arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _identity(llm: Any) -> tuple[str, str]:
    """Copy the labels the client was configured with. Recorded once per bind."""
    settings = getattr(llm, "settings", None)
    model = getattr(settings, "model", None) or getattr(llm, "model", None) or "unknown"
    provider = getattr(llm, "provider", None) or getattr(settings, "provider", None) or type(llm).__name__
    return str(provider), str(model)

def _take_usage(llm: Any) -> tuple[int, int]:
    """Read the token counts the client stored for the call that just returned."""
    raw = getattr(llm, "last_usage", None)
    if hasattr(llm, "last_usage"):
        llm.last_usage = None
    if not isinstance(raw, dict):
        return 0, 0
    try:
        return max(0, int(raw.get("prompt_tokens") or 0)), max(0, int(raw.get("completion_tokens") or 0))
    except (TypeError, ValueError):
        return 0, 0


def _with_usage(turn: AssistantTurn, usage: tuple[int, int]) -> AssistantTurn:
    prompt_tokens, completion_tokens = usage
    if not isinstance(turn, AssistantTurn):
        return turn
    if turn.prompt_tokens or turn.completion_tokens or (prompt_tokens == 0 and completion_tokens == 0):
        return turn
    return AssistantTurn(
        content=turn.content,
        tool_calls=list(turn.tool_calls),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )

def _tool_messages(context: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Harness blocks use tool_use / tool_result. Chat clients speak tool_calls."""
    messages: list[dict[str, Any]] = []
    for message in context:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        if role == "assistant":
            messages.append(_assistant_chat(content))
            continue
        if role != "user":
            continue
        if isinstance(content, str):
            if content.strip():
                messages.append({"role": "user", "content": content})
            continue
        if not isinstance(content, list):
            continue
        texts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                if texts:
                    messages.append({"role": "user", "content": "\n".join(texts)})
                    texts = []
                messages.append(_tool_message(block))
                continue
            text = str(block.get("text") or "").strip() if block.get("type") == "text" else ""
            if text:
                texts.append(text)
        if texts:
            messages.append({"role": "user", "content": "\n".join(texts)})
    return messages


def _assistant_chat(content: Any) -> dict[str, Any]:
    if isinstance(content, str):
        return {"role": "assistant", "content": content}
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and str(block.get("text") or "").strip():
                text_parts.append(str(block["text"]).strip())
            elif block.get("type") == "tool_use":
                raw = block.get("input")
                arguments = raw if isinstance(raw, dict) else {}
                tool_calls.append({
                    "id": block.get("id"),
                    "type": "function",
                    "function": {
                        "name": block.get("name"),
                        "arguments": json.dumps(arguments, ensure_ascii=False),
                    },
                })
    message: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts) or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


def _tool_message(block: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": block.get("tool_use_id"),
        "content": _json_text(block.get("content")),
    }


def _answer_messages(context: list[dict[str, Any]]) -> list[dict[str, str]]:
    """A text call has no tool role. Tool results become plain user text."""
    messages: list[dict[str, str]] = []
    for message in context:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        if role == "assistant":
            text = _plain_text(content)
            if text:
                messages.append({"role": "assistant", "content": text})
            continue
        if role != "user":
            continue
        if isinstance(content, str):
            if content.strip():
                messages.append({"role": "user", "content": content})
            continue
        text = _tool_plain(content)
        if text:
            messages.append({"role": "user", "content": text})
    return messages


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


def _tool_plain(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and str(block.get("text") or "").strip():
            parts.append(str(block["text"]).strip())
        elif block.get("type") == "tool_result":
            parts.append(_json_text(block.get("content")))
    return "\n".join(parts)


def _json_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps({} if value is None else value, ensure_ascii=False)