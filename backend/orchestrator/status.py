"""聊天流上的一个实时步骤。

慢调用之前先产出这个。同一个 `id` 就地更新，所以页面可以把「正在…」换成「完成」，
而不必等整轮结束。

这里收集的列表只属于这一轮。`begin_activity` 开始它，每次 `status` 更新一步，
`note_model_output` 把刚解析的 JSON 挂在当前开着的那一步上。工作台从存下的消息里读回这份列表。
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
