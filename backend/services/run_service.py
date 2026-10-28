"""Business rules for the exercise Run API.

Routes call these functions. Status changes and event names are decided here. Creating a run
writes the run, `run.created`, and the outbox row in one repository call.
"""

from __future__ import annotations

from typing import Any

from ..db import Database
from ..repositories.run_repository import (
    TERMINAL_STATUSES,
    RunStatus,
    append_run_event,
    # create_agent_run,
    create_run_with_outbox,
    find_run_by_id,
    find_run_by_idempotency_key,
    list_run_events,
    run_trace,
    update_run_status,
    request_run_cancel,
)


class AppError(Exception):
    """A business failure with a stable code and an HTTP status."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


def _require_run(db: Database, org_id: str, run_id: str) -> dict[str, Any]:
    run = find_run_by_id(db, org_id, run_id)
    if run is None:
        raise AppError(404, "RUN_NOT_FOUND", "Run not found")
    return run


def create_run(
    db: Database,
    org_id: str,
    user_id: str,
    run_type: str,
    idempotency_key: str,
    run_input: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    existing = find_run_by_idempotency_key(db, org_id, idempotency_key)
    if existing is not None:
        return existing, False
    return create_run_with_outbox(db, org_id, user_id, run_type, idempotency_key, run_input)


def get_run(db: Database, org_id: str, run_id: str) -> dict[str, Any]:
    return _require_run(db, org_id, run_id)


def list_events(db: Database, org_id: str, run_id: str) -> list[dict[str, Any]]:
    events = list_run_events(db, org_id, run_id)
    if events is None:
        raise AppError(404, "RUN_NOT_FOUND", "Run not found")
    return events


def cancel_run(db: Database, org_id: str, run_id: str, reason: str | None = None) -> dict[str, Any]:
    run = _require_run(db, org_id, run_id)
    if run["status"] in TERMINAL_STATUSES:
        raise AppError(409, "RUN_ALREADY_TERMINAL", "Run is already in a terminal state")
    if run["status"] == RunStatus.RUNNING:
        updated = request_run_cancel(db, org_id, run["id"], reason)
        if updated is None:
            raise AppError(404, "RUN_NOT_FOUND", "Run not found")
        if updated["status"] in TERMINAL_STATUSES:
            raise AppError(409, "RUN_ALREADY_TERMINAL", "Run is already in a terminal state")
        return updated
    payload: dict[str, Any] = {}
    if reason:
        payload["reason"] = reason
    updated = update_run_status(db, org_id, run["id"], RunStatus.CANCELLED)
    if updated is None or append_run_event(db, org_id, run["id"], "run.cancelled", payload) is None:
        raise AppError(404, "RUN_NOT_FOUND", "Run not found")
    return updated


def resume_run(
    db: Database, org_id: str, run_id: str, run_input: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run = _require_run(db, org_id, run_id)
    if run["status"] != RunStatus.WAITING_FOR_USER:
        raise AppError(409, "RUN_NOT_WAITING_FOR_USER", "Run is not waiting for user")
    payload: dict[str, Any] = {}
    if run_input is not None:
        payload["input"] = run_input
    updated = update_run_status(db, org_id, run["id"], RunStatus.QUEUED)
    if updated is None or append_run_event(db, org_id, run["id"], "run.resumed", payload) is None:
        raise AppError(404, "RUN_NOT_FOUND", "Run not found")
    return updated


def get_trace(db: Database, org_id: str, run_id: str) -> dict[str, Any]:
    trace = run_trace(db, org_id, run_id)
    if trace is None:
        raise AppError(404, "RUN_NOT_FOUND", "Run not found")
    return trace
