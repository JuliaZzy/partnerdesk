"""Clock, row snapshots, and the run transition table.

`utcnow` is the one timestamp written into `timestamptz` columns.
`InvalidRunTransition` is raised from `Database._transition` and mapped to HTTP 409.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import inspect

from ...config import db_url


def utcnow() -> datetime:
    """The one clock the tables are stamped from — always UTC-aware, so what goes into a
    `timestamptz` column carries its offset instead of inheriting the server's."""
    return datetime.now(UTC)


class InvalidRunTransition(Exception):
    """The locked run cannot take this edge. The HTTP layer maps it to 409."""

    def __init__(self, from_status: str, to_status: str) -> None:
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(f"Invalid transition from {from_status} to {to_status}")

class ChatTurnBusy(Exception):
    """This chat already has a running run. The queue row stays queued."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(run_id)

ALLOWED_RUN_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"running", "cancelled"}),
    "running": frozenset({"succeeded", "failed", "queued", "waiting_for_user", "cancelled"}),
    "waiting_for_user": frozenset({"queued", "cancelled"}),
}


RUN_TRANSITION_EVENTS: dict[tuple[str, str], str] = {
    ("queued", "running"): "run.started",
    ("queued", "cancelled"): "run.cancelled",
    ("running", "succeeded"): "run.completed",
    ("running", "failed"): "run.failed",
    ("running", "queued"): "run.retry_scheduled",
    ("running", "waiting_for_user"): "run.waiting_for_user",
    ("running", "cancelled"): "run.cancelled",
    ("waiting_for_user", "queued"): "run.resumed",
    ("waiting_for_user", "cancelled"): "run.cancelled",
}


def _as_url(target: str | None) -> str:
    return db_url() if target is None else str(target)


def _row_dict(row: Any) -> dict[str, Any]:
    """Column values, skipping ones not loaded. A deferred blob (the uploaded file) stays
    out of every list and detail read that did not ask for it."""
    unloaded = inspect(row).unloaded
    return {c.key: getattr(row, c.key) for c in row.__table__.columns if c.key not in unloaded}


def _history_ids(history: Any, key: str) -> list[str]:
    """Distinct string ids stored on transcript messages, in first-seen order."""
    found: list[str] = []
    if not isinstance(history, list):
        return found
    for message in history:
        if not isinstance(message, dict):
            continue
        value = message.get(key)
        if isinstance(value, str) and value and value not in found:
            found.append(value)
    return found
