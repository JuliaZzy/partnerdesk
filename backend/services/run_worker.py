"""Claim one `job_queue` row and run it.

The run row is read before any execution. A terminal run is acknowledged and left alone.
The lease update and `run.started` commit before `execute` is called. `execute` returns the
`output_json` to store. The queue row is acknowledged only after that commit.

`PermanentRunError` fails the run. Any other exception is retried until `max_attempts`, then
moved to `dead_letter_jobs`.
"""

from __future__ import annotations

import time, logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from sqlalchemy.exc import OperationalError

from ..db import Database, utcnow
from ..repositories.run_repository import (
    TERMINAL_STATUSES,
    ack_job,
    claim_queued_job,
    claim_run,
    complete_run,
    fail_run,
    cancel_run,
    find_agent_run_by_id,
    schedule_retry,
    requeue_job,
)

LEASE_FOR = timedelta(minutes=5) # How long to keep the lease on the row.
RETRY_AFTER = timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15)

log = logging.getLogger(__name__)

class PermanentRunError(Exception):
    """The input or the permission will not succeed on another try."""

class InfrastructureError(Exception):
    """The queue message can be tried by another worker. This does not spend a business attempt."""

def work_once(
    db: Database,
    worker_id: str,
    execute: Callable[[dict[str, Any]], dict[str, Any]],
    retry_after: tuple[timedelta, ...] = RETRY_AFTER,
) -> bool:
    """Handle one queue row. False when the queue has nothing queued."""
    job = claim_queued_job(db, worker_id)
    if job is None:
        return False
    try:
        run_id = _run_id(job)
    except ValueError as e:
        return _settle(db, job["id"], "", e, permanent=True, retry_after=retry_after)
    try:
        run = find_agent_run_by_id(db, run_id)
        if run is None or run["status"] in TERMINAL_STATUSES:
            ack_job(db, job["id"])
            return True
        claimed = claim_run(db, run_id, worker_id, _lease_deadline())
        if claimed is None:
            ack_job(db, job["id"])
            return True
        output = execute(claimed)
    except (InfrastructureError, OperationalError) as e:
        requeue_job(db, job["id"], run_id, str(e))
        return True
    except PermanentRunError as e:
        return _settle(db, job["id"], run_id, e, permanent=True, retry_after=retry_after)
    except Exception as e:
        return _settle(db, job["id"], run_id, e, permanent=False, retry_after=retry_after)
    finished = complete_run(db, run_id, output)
    if finished is None:
        cancel_run(db, run_id)
    ack_job(db, job["id"])
    return True

def serve_worker(
    db: Database, 
    worker_id: str, 
    execute: Callable[[dict[str, Any]], dict[str, Any]]
) -> None:
    """Keep taking queue rows. Do not call this from `create_app` or from tests."""
    while True:
        if not work_once(db, worker_id, execute):
            time.sleep(0.5)

def _settle(
    db: Database,
    job_id: str,
    run_id: str,
    exc: Exception,
    permanent: bool,
    retry_after: tuple[timedelta, ...],
) -> bool:
    error = {
        "kind": "permanent" if permanent else "transient",
        "message": str(exc),
    }
    if not run_id:
        ack_job(db, job_id)
        return True
    outcome = fail_run(db, job_id, run_id, error) if permanent else schedule_retry(db, job_id, run_id, error, retry_after)
    if outcome is None:
        ack_job(db, job_id)
    elif outcome == "dead_letter":
        log.error('dead letter run_id=%s job_id=%s error=%s', run_id, job_id, error.get("message"))
    return True

def _run_id(job: dict[str, Any]) -> str:
    payload = job["payload_json"]
    run_id = payload.get("run_id") if isinstance(payload, dict) else None 
    if not isinstance(run_id, str) or not run_id:
        raise ValueError(f"execute_run job {job['id']} has no run_id")
    return run_id

def _lease_deadline() -> datetime:
    return utcnow() + LEASE_FOR