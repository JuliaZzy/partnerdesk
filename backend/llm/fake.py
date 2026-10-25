"""Scripted stand-in for tests. No network.

`json_replies` are consumed in order. `text_reply` streams word by word.
`tool_replies` are consumed by `complete`. Every call is appended to `calls`.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from .types import AssistantTurn


@dataclass
class FakeLLM:
    """Scripted model for tests. `json_replies` are consumed in order; `text_reply` streams
    word by word. Every call is recorded so a test can assert on the prompt it received."""

    json_replies: list[dict[str, Any]] = field(default_factory=list)
    tool_replies: list[AssistantTurn] = field(default_factory=list)
    text_reply: str = "Got it."
    calls: list[dict[str, Any]] = field(default_factory=list)
    fail_json: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def complete_json(self, *, system, messages, schema=None, temperature=0.0, max_tokens=900):
        with self._lock:
            self.calls.append({"kind": "json", "system": system, "messages": messages, "schema": schema})
            if self.fail_json:
                raise RuntimeError("model unavailable")
            if not self.json_replies:
                raise RuntimeError("FakeLLM: no scripted JSON reply left")
            return self.json_replies.pop(0)

    def stream_text(self, *, system, messages, temperature=0.7, max_tokens=300):
        self.calls.append({"kind": "text", "system": system, "messages": messages})
        for i, word in enumerate(self.text_reply.split(" ")):
            yield (" " if i else "") + word

    def complete(self, *, system, messages, tools=None, temperature=0.0, max_tokens=900):
        self.calls.append({"kind": "complete", "system": system, "messages": messages, "tools": tools})
        if self.tool_replies:
            return self.tool_replies.pop(0)
        return AssistantTurn()
