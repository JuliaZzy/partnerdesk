"""One status frame on the chat stream.

The desk turn writes the frame into the run trace when it yields it. The same
`id` is one step: `running` opens it, `done` closes it. Nothing here is kept
in memory or copied onto the session message.
"""

from __future__ import annotations

from typing import Any


def status(step_id: str, title: str, detail: str, *, state: str) -> dict[str, Any]:
    return {"type": "status", "id": step_id, "title": title, "detail": detail, "state": state}
