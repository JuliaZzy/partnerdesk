"""Outbox delivery and the `job_queue` worker claims.

Each method is its own transaction. Publishing a job and marking the outbox
row dispatched stay separate, so a crash between them leaves the outbox pending.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import orm
from .support import _row_dict, utcnow


class QueueMixin:
    def find_pending_outbox_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        """Pending outbox rows whose `next_attempt_at` has arrived, oldest first."""
        now = utcnow()
        with self.tx() as s:
            rows = s.scalars(
                select(orm.OutboxJob)
                .where(orm.OutboxJob.status == "pending", orm.OutboxJob.next_attempt_at <= now)
                .order_by(orm.OutboxJob.next_attempt_at)
                .limit(limit)
            )
            return [_row_dict(row) for row in rows]

    def enqueue_job(self, job_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Insert one `job_queue` row. This is the publish step, in its own transaction."""
        now = utcnow()
        with self.tx() as s:
            job = orm.JobQueue(
                id=f"job_{uuid.uuid4().hex}",
                job_type=job_type,
                payload_json=payload,
                status="queued",
                available_at=now,
                created_at=now,
            )
            s.add(job)
            s.flush()
            return _row_dict(job)

    def mark_outbox_retry(self, outbox_id: str, error: str) -> dict[str, Any] | None:
        """Push a pending outbox row forward after a failed publish. 
        
        Past `max_attempts` the outbox row is `failed` and one `dead_letter_jobs` row is written, 
        and a still-queued run is moved running then failed in this transaction. 
        """
        now = utcnow()
        cap_seconds = timedelta(minutes=15).total_seconds()
        with self.tx() as s:
            job = s.scalars(
                select(orm.OutboxJob)
                .where(orm.OutboxJob.id == outbox_id)
                .with_for_update()
            ).first()
            if job is None or job.status != "pending":
                return None
            attempts = int(job.attempts) + 1
            job.attempts = attempts
            job.last_error_json = {"message": error}
            if attempts > int(job.max_attempts):
                job.status = "failed"
                payload = job.payload_json if isinstance(job.payload_json, dict) else {}
                run_id = payload.get("run_id")
                run = s.scalars(
                    select(orm.AgentRun)
                    .where(orm.AgentRun.id == run_id)
                    .with_for_update()
                ).first() if isinstance(run_id, str) else None
                failure = {"kind": "dead_letter", "message": error}
                if run is not None and run.status == "queued":
                    self._transition(
                        s, run, "running", {"reason": "outbox_dead_letter"}, now, 
                        actor_type = "dispatcher", actor_id = "outbox"
                    )
                    self._transition(s, run, "failed", {"reason": "dead_letter"}, now, error=failure, 
                        actor_type = "dispatcher", actor_id = "outbox"
                    )
                s.add(orm.DeadLetterJob(
                    id=f"dlj_{uuid.uuid4().hex}",
                    original_job_id=job.id,
                    job_type=job.job_type,
                    payload_json=job.payload_json or {},
                    attempts=attempts,
                    error_json=failure,
                    created_at=now,
                ))
                s.flush()
                return _row_dict(job)
            delay_seconds = min(cap_seconds, 2 ** (attempts - 1))
            job.next_attempt_at = now + timedelta(seconds=delay_seconds)
            s.flush()
            return _row_dict(job)

    def list_dead_letter_jobs(self, original_job_id: str) -> list[dict[str, Any]]:
        """Dead-letter rows for one outbox or queue id, oldest first."""
        with self.tx() as s:
            rows = s.scalars(
                select(orm.DeadLetterJob)
                .where(orm.DeadLetterJob.original_job_id == original_job_id)
                .order_by(orm.DeadLetterJob.created_at, orm.DeadLetterJob.id)
            )
            return [_row_dict(row) for row in rows]

    def claim_queued_job(self, worker_id: str, run_id: str | None = None) -> dict[str, Any] | None:
        """Lock one queued execute_run job and mark it running. None when nothing is due."""
        now = utcnow()
        with self.tx() as s:
            filters = [
                orm.JobQueue.status == "queued",
                orm.JobQueue.job_type == "execute_run",
                orm.JobQueue.available_at <= now
            ]
            if run_id is not None:
                filters.append(orm.JobQueue.payload_json["run_id"].as_string() == run_id)
            job = s.scalars(
                select(orm.JobQueue)
                .where(*filters)
                .order_by(orm.JobQueue.available_at, orm.JobQueue.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            ).first()
            if job is None:
                return None
            job.status = "running"
            job.lock_by = worker_id
            s.flush()
            return _row_dict(job)

    def mark_outbox_dispatched(self, outbox_id: str) -> dict[str, Any] | None:
        """Mark one outbox row as dispatched. This is the consume step, in its own transaction."""
        now = utcnow()
        with self.tx() as s:
            job = s.scalars(
                select(orm.OutboxJob).where(orm.OutboxJob.id == outbox_id).with_for_update()
            ).first()
            if job is None or job.status != "pending":
                return None
            payload = job.payload_json if isinstance(job.payload_json, dict) else {}
            run_id = payload.get("run_id")
            run = s.get(orm.AgentRun, run_id) if isinstance(run_id, str) else None
            if run is None:
                return None
            job.status = "dispatched"
            job.dispatch_at = now
            self._append_event(s, run_id, "run.enqueued", {"outbox_job_id": job.id}, now)
            s.flush()
            return _row_dict(job)

    def ack_job(self, job_id: str) -> dict[str, Any] | None:
        """Mark a queue row finished. Call this only after the run row has committed."""
        now = utcnow()
        with self.tx() as s:
            job = s.scalars(
                select(orm.JobQueue)
                .where(orm.JobQueue.id == job_id, orm.JobQueue.status == "running")
                .with_for_update()
            ).first()
            if job is None:
                return None
            job.status = "succeeded"
            job.finished_at = now
            s.flush()
            return _row_dict(job)

    def requeue_job(self, job_id: str, run_id: str, error: str) -> dict[str, Any] | None: 
        """Put a running queue row back. A still-running run goes back to queued so it can be claimed again."""
        now = utcnow()
        with self.tx() as s:
            job = s.scalars(
                select(orm.JobQueue)
                .where(orm.JobQueue.id == job_id, orm.JobQueue.status == "running")
                .with_for_update()
            ).first()
            if job is None:
                return None
            job.status = "queued"
            job.lock_by = None
            job.lock_until = None
            job.available_at = now + timedelta(seconds=5)
            job.last_error_json = {"kind": "infrastructure", "message": error}
            if run_id:
                run = s.scalars(
                    select(orm.AgentRun)
                    .where(orm.AgentRun.id == run_id, orm.AgentRun.status == "running")
                    .with_for_update()
                ).first()
                if run is not None:
                    self._transition(s, run, "queued", {"kind": "infrastructure", "message": error}, now, error={"kind": "infrastructure", "message": error})
            s.flush()
            return _row_dict(job)

    def _lock_running_pair(
        self, s: Session, job_id: str, run_id: str
    ) -> tuple[orm.JobQueue | None, orm.AgentRun | None]:
        job = s.scalars(
            select(orm.JobQueue)
            .where(orm.JobQueue.id == job_id, orm.JobQueue.status == "running")
            .with_for_update()
        ).first()
        run = s.scalars(
            select(orm.AgentRun)
            .where(orm.AgentRun.id == run_id, orm.AgentRun.status == "running")
            .with_for_update()
        ).first()
        if job is None or run is None:
            return None
        return job, run

    def fail_run(self, job_id: str, run_id: str, error: dict[str, Any]) -> dict[str, Any] | None:
        """Permanent failure: fail the run, append `run.failed`, and finish the queue row."""
        now = utcnow()
        with self.tx() as s:
            locked = self._lock_running_pair(s, job_id, run_id)
            if locked is None:
                return None
            job, run = locked
            self._fail_locked_run(s, job, run, error, "permanent", now)
            s.flush()
            return _row_dict(run)

    def schedule_retry(
        self, 
        job_id: str, 
        run_id: str,
        error: dict[str, Any],
        delays: tuple[timedelta, ...],
    ) -> str | None:
        """Count one transient failure. `retry` queues it again; `dead letter` stops it.

        None when the run is no longer running, for example because it was cancelled.
        """
        now = utcnow()
        with self.tx() as s:
            locked = self._lock_running_pair(s, job_id, run_id)
            if locked is None:
                return None
            job, run = locked
            attempts = int(job.attempts) + 1
            job.attempts = attempts
            job.last_error_json = error
            if attempts > int(job.max_attempts):
                self._fail_locked_run(s, job, run, error, "dead_letter", now)
                s.add(orm.DeadLetterJob(
                    id=f"dlj_{uuid.uuid4().hex}", 
                    original_job_id=job.id, 
                    job_type=job.job_type,
                    payload_json=job.payload_json,
                    attempts=attempts,
                    error_json=error,
                    created_at=now
                ))
                s.flush()
                return "dead_letter"
            delay = delays[min(attempts, len(delays)) - 1]
            available_at = now + delay
            job.status = "queued"
            job.available_at = available_at
            job.lock_by = None
            self._transition(s, run, "queued", {"attempts": attempts, "available_at": available_at.isoformat()}, now, error=error)
            s.flush()
            return "retry"

    def _fail_locked_run(
        self, 
        s: Session, 
        job: orm.JobQueue, 
        run: orm.AgentRun, 
        error: dict[str, Any], 
        reason: str, 
        now: datetime
    ) -> None:
        self._transition(s, run, "failed", {"status": "failed", "reason": reason}, now, error=error)
        job.status = "failed"
        job.finished_at = now
        job.last_error_json = error
