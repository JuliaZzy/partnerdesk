"""One place that picks a model and calls it.

Campus `deepseek-chat` is the small text model. Campus `deepseek-reasoner` is the
same model with thinking on, for report planning and prose. Campus `minimax` plans
a turn. Campus `qwen` reads an order photo. Official `deepseek-flash` writes strict
JSON, Python, and tool calls, with thinking off. None of these calls set `max_tokens`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from ..config import LLMSettings
from .client import DeepSeekClient, SJTUClient
from .parse import parse_json_loose
from .types import AssistantTurn, ToolCall

SCHOOL_CHAT = "deepseek-chat"
SCHOOL_REASONER = "deepseek-reasoner"
SCHOOL_PLAN = "minimax"
SCHOOL_VISION = "qwen"
FLASH = "deepseek-flash"


def _settings() -> LLMSettings:
    return LLMSettings(base_url=None, api_key="")


def _school_text(resp: Any) -> str:
    try:
        return str(resp["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError) as err:
        raise ValueError("Campus model did not return a message.") from err


def school_json(
    model: str, *, system: str, messages: list[dict[str, Any]],
    schema: dict[str, Any] | None = None, temperature: float = 0,
) -> dict[str, Any]:
    """Campus chat. The gateway has no JSON schema; the reply text is the object."""
    del schema
    # Thinking is on for this model name, so leave temperature at the gateway default.
    resp = SJTUClient(_settings()).chat_completion(
        model,
        [{"role": "system", "content": system}, *messages],
        temperature=None if model == SCHOOL_REASONER else temperature,
    )
    raw = parse_json_loose(_school_text(resp))
    if not isinstance(raw, dict):
        raise ValueError("Campus model did not return a JSON object.")
    return raw


def school_text(
    model: str, *, system: str, messages: list[dict[str, Any]], temperature: float = 0,
) -> Iterator[str]:
    """Campus chat, one chunk. The desk still renders it as reply tokens."""
    resp = SJTUClient(_settings()).chat_completion(
        model,
        [{"role": "system", "content": system}, *messages],
        temperature=temperature,
    )
    text = _school_text(resp).strip()
    if text:
        yield text


def _flash_input(messages: list[dict[str, Any]]) -> str | list[dict[str, Any]]:
    if len(messages) == 1 and isinstance(messages[0].get("content"), str):
        return messages[0]["content"]
    return messages


def flash_json(
    *, system: str, messages: list[dict[str, Any]],
    schema: dict[str, Any] | None = None, temperature: float = 0,
) -> dict[str, Any]:
    """Official deepseek-flash, thinking off. A schema is sent when the caller has one."""
    text: dict[str, Any]
    if schema:
        text = {"format": {"type": "json_schema", "name": schema["name"], "schema": schema["schema"]}}
    else:
        text = {"format": {"type": "json_object"}}
    resp = DeepSeekClient(_settings()).response(
        model=FLASH,
        instructions=system,
        input=_flash_input(messages),
        text=text,
        reasoning_effort="none",
        temperature=temperature,
    )
    raw = parse_json_loose(resp.output_text)
    if not isinstance(raw, dict):
        raise ValueError("model did not return a JSON object")
    return raw


def _tool_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def flash_tools(
    *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
) -> AssistantTurn:
    """Official deepseek-flash function calls. Thinking stays off."""
    resp = DeepSeekClient(_settings()).chat_completion(
        model=FLASH,
        messages=[{"role": "system", "content": system}, *messages],
        tools=tools,
        tool_choice="auto",
        reasoning_effort="none",
        temperature=0,
    )
    message = resp.choices[0].message
    calls = [
        ToolCall(id=call.id, name=call.function.name, arguments=_tool_arguments(call.function.arguments))
        for call in (message.tool_calls or [])
    ]
    return AssistantTurn(content=message.content, tool_calls=calls)
