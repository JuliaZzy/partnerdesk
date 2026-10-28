"""SQL store for the exercise Run API.

The session and the queries live on `Database`. Each function is one read or one write.
Routes call the service, and the service decides which of these to call together.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from ..db import Database


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_USER = "waiting_for_user"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset({
    RunStatus.SUCCEEDED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
})


def find_run_by_id(db: Database, org_id: str, run_id: str) -> dict[str, Any] | None:
    return db.find_agent_run(org_id, run_id)


def find_run_by_idempotency_key(db: Database, org_id: str, idempotency_key: str) -> dict[str, Any] | None:
    return db.find_agent_run_by_idempotency(org_id, idempotency_key)


def create_agent_run(
    db: Database,
    org_id: str,
    user_id: str,
    run_type: str,
    idempotency_key: str,
    run_input: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    return db.create_agent_run(org_id, user_id, run_type, idempotency_key, run_input)


def update_run_status(db: Database, org_id: str, run_id: str, status: str) -> dict[str, Any] | None:
    return db.update_run_status(org_id, run_id, status)

def request_run_cancel(db: Database, org_id: str, run_id: str, reason: str | None) -> dict[str, Any] | None:
    return db.request_run_cancel(org_id, run_id, reason)

def cancel_run(db: Database, run_id: str) -> dict[str, Any] | None:
    return db.cancel_run(run_id)

def list_run_events(db: Database, org_id: str, run_id: str) -> list[dict[str, Any]] | None:
    return db.list_run_events(org_id, run_id)


def append_run_event(
    db: Database, org_id: str, run_id: str, event_type: str, payload: dict[str, Any],
) -> dict[str, Any] | None:
    return db.append_run_event(org_id, run_id, event_type, payload)


def run_trace(db: Database, org_id: str, run_id: str) -> dict[str, Any] | None:
    return db.run_trace(org_id, run_id)

def create_run_with_outbox(
    db: Database,
    org_id: str,
    user_id: str,
    run_type: str,
    idempotency_key: str,
    run_input: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    return db.create_run_with_outbox(org_id, user_id, run_type, idempotency_key, run_input)


def find_pending_outbox_jobs(db: Database, limit: int = 100) -> list[dict[str, Any]]:
    return db.find_pending_outbox_jobs(limit)


def enqueue_job(db: Database, job_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return db.enqueue_job(job_type, payload)

def requeue_job(db: Database, job_id: str, run_id: str, error: str) -> dict[str, Any] | None:
    return db.requeue_job(job_id, run_id, error)

def mark_outbox_dispatched(db: Database, outbox_id: str) -> dict[str, Any] | None:
    return db.mark_outbox_dispatched(outbox_id)

def mark_outbox_retry(db: Database, outbox_id: str, error: str) -> dict[str, Any] | None:
    return db.mark_outbox_retry(outbox_id, error)

def claim_queued_job(db: Database, worker_id: str) -> dict[str, Any] | None:
    return db.claim_queued_job(worker_id)


def find_agent_run_by_id(db: Database, run_id: str) -> dict[str, Any] | None:
    return db.find_agent_run_by_id(run_id)


def claim_run(
    db: Database, run_id: str, worker_id: str, lease_expires_at: datetime,
) -> dict[str, Any] | None:
    return db.claim_run(run_id, worker_id, lease_expires_at)


def complete_run(db: Database, run_id: str, output: dict[str, Any]) -> dict[str, Any] | None:
    return db.complete_run(run_id, output)


def ack_job(db: Database, job_id: str) -> dict[str, Any] | None:
    return db.ack_job(job_id)

def fail_run(
    db: Database, job_id: str, run_id: str, error: dict[str, Any],
) -> dict[str, Any] | None:
    return db.fail_run(job_id, run_id, error)

def schedule_retry(
    db: Database,
    job_id: str,
    run_id: str,
    error: dict[str, Any],
    delays: tuple[timedelta, ...],
) -> str | None:
    return db.schedule_retry(job_id, run_id, error, delays)