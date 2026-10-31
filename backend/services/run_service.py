"""Business rules for the exercise Run API.

Routes call these functions. Status changes and event names are decided here. Creating a run
writes the run, `run.created`, and the outbox row in one repository call.
"""

from __future__ import annotations

from typing import Any

from ..db import Database, InvalidRunTransition
from ..repositories.run_repository import (
    RUN_TERMINAL_STATUSES,
    RunStatus,
    create_agent_run,
    find_run_by_id,
    find_run_by_idempotency_key,
    list_run_events,
    request_run_cancel,
    run_trace,
    transition_run,
)
from .run_names import RunType


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

def _transition_rejected(exc: InvalidRunTransition) -> AppError:
    if exc.from_status in RUN_TERMINAL_STATUSES:
        return AppError(409, "RUN_ALREADY_TERMINAL", "Run is already in a terminal state")
    if exc.to_status == RunStatus.QUEUED:
        return AppError(409, "RUN_NOT_WAITING_FOR_USER", "Run is not waiting for user")
    return AppError(409, "INVALID_RUN_TRANSITION", f"Cannot move a run from {exc.from_status} to {exc.to_status}")

def _request_cancel(
    db: Database, org_id: str, run_id: str, reason: str | None = None
) -> dict[str, Any]:
    updated = request_run_cancel(db, org_id, run_id, reason)
    if updated is None:
        raise AppError(404, "RUN_NOT_FOUND", "Run not found")
    if updated["status"] in RUN_TERMINAL_STATUSES:
        raise AppError(409, "RUN_ALREADY_TERMINAL", "Run is already in a terminal state")
    return updated

def create_run(
    db: Database,
    org_id: str,
    user_id: str,
    run_type: str,
    idempotency_key: str,
    run_input: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    try:
        RunType(run_type)
    except ValueError:
        raise AppError(
            422, 
            "INVALID_RUN_TYPE", 
            "Invalid run type"
        ) from None 
    existing = find_run_by_idempotency_key(db, org_id, idempotency_key)
    if existing is not None:
        return existing, False
    return create_agent_run(db, org_id, user_id, run_type, idempotency_key, run_input)


def rerun(
    db: Database,
    org_id: str,
    user_id: str,
    run_id: str,
    idempotency_key: str,
    run_input: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool]:
    """Start a new queued run from an existing one. The parent row is left as it is."""
    parent = find_run_by_id(db, org_id, run_id)
    if parent is None:
        raise AppError(404, "RUN_NOT_FOUND", "Run not found")
    existing = find_run_by_idempotency_key(db, org_id, idempotency_key)
    if existing is not None:
        return existing, False
    copied = parent["input_json"] if run_input is None else run_input
    return create_agent_run(
        db, org_id, user_id, parent["run_type"], idempotency_key, copied, parent["id"],
    )


def get_run(db: Database, org_id: str, run_id: str) -> dict[str, Any]:
    return _require_run(db, org_id, run_id)


def list_events(db: Database, org_id: str, run_id: str) -> list[dict[str, Any]]:
    events = list_run_events(db, org_id, run_id)
    if events is None:
        raise AppError(404, "RUN_NOT_FOUND", "Run not found")
    return events


def cancel_run(db: Database, org_id: str, run_id: str, reason: str | None = None) -> dict[str, Any]:
    run = _require_run(db, org_id, run_id)
    if run["status"] in RUN_TERMINAL_STATUSES:
        raise AppError(409, "RUN_ALREADY_TERMINAL", "Run is already in a terminal state")
    if run["status"] == RunStatus.RUNNING:
        return _request_cancel(db, org_id, run["id"], reason)
    payload: dict[str, Any] = {}
    if reason:
        payload["reason"] = reason
    try:
        updated = transition_run(db, org_id, run["id"], RunStatus.CANCELLED, payload, from_status=frozenset({RunStatus.QUEUED, RunStatus.WAITING_FOR_USER}))
    except InvalidRunTransition as e:
        if e.from_status == RunStatus.RUNNING:
            return _request_cancel(db, org_id, run["id"], reason)
        raise _transition_rejected(e) from e
    if updated is None:
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
    try:
        updated = transition_run(db, org_id, run["id"], RunStatus.QUEUED, payload, from_status=frozenset({RunStatus.WAITING_FOR_USER}))
    except InvalidRunTransition as e:
        raise _transition_rejected(e) from e
    if updated is None:
        raise AppError(404, "RUN_NOT_FOUND", "Run not found")
        
    from .chat_run import wake_chat_worker
    from .dispatcher import dispatch_pending

    dispatch_pending(db)
    wake_chat_worker(run_id)
    return updated

def get_trace(db: Database, org_id: str, run_id: str) -> dict[str, Any]:
    trace = run_trace(db, org_id, run_id)
    if trace is None:
        raise AppError(404, "RUN_NOT_FOUND", "Run not found")
    return trace
