"""One live step on the chat stream.

Yield this before a slow call. The same `id` updates in place, so the page can
turn "正在…" into "完成" without waiting for the whole turn.
"""

from __future__ import annotations

from typing import Any


def status(step_id: str, title: str, detail: str, *, state: str) -> dict[str, Any]:
    return {"type": "status", "id": step_id, "title": title, "detail": detail, "state": state}
