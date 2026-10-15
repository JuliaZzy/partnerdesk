"""Vendor MCP servers (stdio / http). Tools are exposed on the same `tools=` surface as natives."""

from __future__ import annotations

from .runtime import McpHub, get_hub

__all__ = ["McpHub", "get_hub"]
