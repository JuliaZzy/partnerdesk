"""One live step on the chat stream.

Yield this before a slow call. The same `id` updates in place, so the page can
turn "正在…" into "完成" without waiting for the whole turn.

The list collected here is this turn only. `begin_activity` starts it, each
`status` updates one step, and `note_model_output` hangs the JSON just parsed
on the step that is open. The desk reads that list back off the saved message.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_activity: ContextVar[list[dict[str, Any]] | None] = ContextVar("partnerdesk_activity", default=None)


def begin_activity() -> None:
    """Start a fresh step list for this turn. A copied request context must not keep the last one."""
    _activity.set([])


def status(step_id: str, title: str, detail: str, *, state: str) -> dict[str, Any]:
    steps = _activity.get()
    if steps is None:
        steps = []
        _activity.set(steps)
    found = next((step for step in steps if step["id"] == step_id), None)
    if found is None:
        steps.append({
            "id": step_id, "title": title, "detail": detail, "state": state, "outputs": [],
        })
    else:
        found["title"] = title
        found["detail"] = detail
        found["state"] = state
    return {"type": "status", "id": step_id, "title": title, "detail": detail, "state": state}


def note_model_output(parsed: dict[str, Any]) -> None:
    """Attach one JSON object to the step that is open. A call before any step stays on the message only."""
    steps = _activity.get()
    if not steps or not isinstance(parsed, dict):
        return
    outputs = steps[-1].get("outputs")
    if isinstance(outputs, list):
        outputs.append(parsed)


def current_activity() -> list[dict[str, Any]]:
    steps = _activity.get() or []
    return [{**step, "outputs": list(step.get("outputs") or [])} for step in steps]
