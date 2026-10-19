"""Callable tools for native function calling (`tools=` / tool_choice=auto).

Each native module exposes `run(arguments) -> {ok, data, error}` and a SCHEMA.
MCP vendor tools are merged at call time from `backend.mcp` so the model fills
arguments the same way — it never sees API keys.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import exchange_rate, lang_translate, send_email, web_search

ToolFn = Callable[..., dict[str, Any]]

# Built-in tools always available.
TOOLS: dict[str, ToolFn] = {
    "web_search": web_search.run,
    "translate": lang_translate.run,
    "exchange_rate": exchange_rate.run,
    "send_email": send_email.run,
}

SCHEMAS: list[dict[str, Any]] = [
    web_search.SCHEMA, lang_translate.SCHEMA, exchange_rate.SCHEMA, send_email.SCHEMA,
]


def all_schemas(*, include_mcp: bool | None = None) -> list[dict[str, Any]]:
    """OpenAI `tools=` list: natives first, then MCP when a key is configured."""
    out = list(SCHEMAS)
    if include_mcp is False:
        return out
    try:
        from ..mcp.runtime import get_hub, mcp_enabled

        if include_mcp is None and not mcp_enabled():
            return out
        out.extend(get_hub().schemas)
    except Exception:
        pass
    return out


def all_tools(*, include_mcp: bool | None = None) -> dict[str, ToolFn]:
    """name → runner, including MCP tools when available."""
    out = dict(TOOLS)
    if include_mcp is False:
        return out
    try:
        from ..mcp.runtime import get_hub, make_mcp_tool_fn, mcp_enabled

        if include_mcp is None and not mcp_enabled():
            return out
        hub = get_hub()
        for schema in hub.schemas:
            name = schema["function"]["name"]
            out[name] = make_mcp_tool_fn(hub, name)
    except Exception:
        pass
    return out
