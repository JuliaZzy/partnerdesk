"""Agent runs, the status machine, and the trace read.

Status is assigned only in `_transition`. `_append_event` and `_append_audit`
share the caller's transaction. A single leading underscore keeps those helpers
visible to the other mixins on `Database`; a double underscore would be renamed
per class and the queue mixin could not call them.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import orm
from .support import (
    ALLOWED_RUN_TRANSITIONS,
    RUN_TRANSITION_EVENTS,
    ChatTurnBusy,
    InvalidRunTransition,
    _row_dict,
    utcnow,
)


class RunMixin:
    def find_agent_run(self, org_id: str, run_id: str) -> dict[str, Any] | None:
        """Load one run. `org_id` is part of the lookup, so another org's id looks missing."""
        with self.tx() as s:
            row = s.scalars(
                select(orm.AgentRun).where(orm.AgentRun.id == run_id, orm.AgentRun.org_id == org_id)
            ).first()
            return _row_dict(row) if row else None

    def find_agent_run_by_idempotency(self, org_id: str, idempotency_key: str) -> dict[str, Any] | None:
        with self.tx() as s:
            row = s.scalars(
                select(orm.AgentRun).where(
                    orm.AgentRun.org_id == org_id, orm.AgentRun.idempotency_key == idempotency_key,
                )
            ).first()
            return _row_dict(row) if row else None

    def list_run_events(self, org_id: str, run_id: str) -> list[dict[str, Any]] | None:
        """Events in `sequence` order, or None when this org has no such run."""
        with self.tx() as s:
            owned = s.scalars(
                select(orm.AgentRun.id).where(orm.AgentRun.id == run_id, orm.AgentRun.org_id == org_id)
            ).first()
            if owned is None:
                return None
            rows = s.scalars(
                select(orm.RunEvent).where(orm.RunEvent.run_id == run_id).order_by(orm.RunEvent.sequence)
            )
            return [_row_dict(row) for row in rows]

    def transition_run(
        self,
        org_id: str,
        run_id: str,
        to_status: str,
        payload: dict[str, Any] | None = None,
        *,
        from_status: frozenset[str] | None = None,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Move one run along a legal edge and append that edge's event, in one transaction.
        None when this org has no such run. `from_statuses` narrows which current
        statuses the caller accepts; the whitelist still applies. An illegal edge
        raises InvalidRunTransition and the transaction rolls back.
        """
        now = utcnow()
        with self.tx() as s:
            run = s.scalars(
                select(orm.AgentRun)
                .where(orm.AgentRun.id == run_id, orm.AgentRun.org_id == org_id)
                .with_for_update()
            ).first()
            if run is None:
                return None
            if from_status is not None and run.status not in from_status:
                raise InvalidRunTransition(run.status, to_status)
            self._transition(
                s, run, to_status, payload or {}, now, error=error, actor_type="api", actor_id=run.user_id,
            )
            s.flush()
            return _row_dict(run)

    def _transition(
        self,
        s: Session,
        run: orm.AgentRun,
        to_status: str,
        payload: dict[str, Any],
        now: datetime,
        *,
        lease_owner: str | None = None,
        lease_expires_at: datetime | None = None,
        output: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        actor_type: str = "worker",
        actor_id: str | None = None,
    ) -> None:
        """The one place that assigns `agent_runs.status`. The row is already locked."""
        source = run.status
        if to_status not in ALLOWED_RUN_TRANSITIONS.get(source, frozenset()):
            raise InvalidRunTransition(source, to_status)
        who = actor_id or lease_owner or run.lease_owner or run.user_id
        self._apply_status_fields(run, to_status, now, lease_owner=lease_owner, lease_expires_at=lease_expires_at, output=output, error=error)
        run.status = to_status
        run.updated_at = now
        event_payload = {
            "from_status": source,
            "to_status": to_status,
            "actor_type": actor_type,
            "actor_id": who,
        }
        event_payload.update(payload)
        self._append_event(s, run.id, RUN_TRANSITION_EVENTS[(source, to_status)], event_payload, now)
        self._append_audit(s, run, event_payload, now, who)
        if source == "waiting_for_user" and to_status == "queued":
            s.add(orm.OutboxJob(
                id=f"obx_{uuid.uuid4().hex}",
                job_type="execute_run",
                payload_json={"run_id": run.id},
                status="pending",
                next_attempt_at=now,
                created_at=now,
                dispatch_at=now,
            ))
            s.flush()

    def _apply_status_fields(
        self,
        run: orm.AgentRun,
        to_status: str,
        now: datetime,
        *,
        lease_owner: str | None = None,
        lease_expires_at: datetime | None = None,
        output: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        """Columns that ride along with the status. The event is written by `_transition`.
        `agent_runs.attempts` counts entries into `running`. The retry budget that
        dead-letters a job is still `job_queue.attempts`, counted in `schedule_retry`.
        """
        if to_status == "running":
            if run.started_at is None:
                run.started_at = now
            run.attempts = int(run.attempts) + 1
            run.lease_owner = lease_owner
            run.lease_expires_at = lease_expires_at
        elif to_status == "waiting_for_user":
            run.lease_owner = None
            run.lease_expires_at = None
        elif to_status == "queued":
            run.lease_owner = None
            run.lease_expires_at = None
            if error is not None:
                run.error_json = error
        elif to_status == "succeeded":
            run.output_json = output
            run.ended_at = now
        elif to_status == "failed":
            run.error_json = error
            run.ended_at = now
            run.lease_owner = None
            run.lease_expires_at = None
        elif to_status == "cancelled":
            run.ended_at = now
            run.lease_owner = None
            run.lease_expires_at = None

    def request_run_cancel(
        self,
        org_id: str,
        run_id: str,
        reason: str | None,
    ) -> dict[str, Any] | None:
        """Mark a running run for cancel. Status stays `running`. None when this org has no such run."""
        now = utcnow()
        with self.tx() as s:
            run = s.scalars(
                select(orm.AgentRun)
                .where(orm.AgentRun.id == run_id, orm.AgentRun.org_id == org_id)
                .with_for_update()
            ).first()
            if run is None:
                return None
            if run.status != "running":
                return _row_dict(run)
            run.cancel_requested_at = now
            run.updated_at = now
            payload: dict[str, Any] = {"status": "running"}
            if reason:
                payload["reason"] = reason
            self._append_event(s, run_id, "run.cancel_requested", payload, now)
            s.flush()
            return _row_dict(run)

    def cancel_run(self, run_id: str) -> dict[str, Any] | None:
        """Worker path: running to cancelled, plus `run.cancelled`. None unless the flag is set."""
        now = utcnow()
        with self.tx() as s:
            run = s.scalars(
                select(orm.AgentRun)
                .where(orm.AgentRun.id == run_id, orm.AgentRun.status == "running")
                .with_for_update()
            ).first()
            if run is None or run.cancel_requested_at is None:
                return None
            self._transition(s, run, "cancelled", {"status": "cancelled"}, now)
            s.flush()
            return _row_dict(run)

    @staticmethod 
    def _append_event(
        s: Session,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
        now: datetime,
    ) -> orm.RunEvent:
        current = s.scalar(
            select(func.max(orm.RunEvent.sequence)).where(orm.RunEvent.run_id == run_id)
        )
        event = orm.RunEvent(
            id=f"evt_{uuid.uuid4().hex}",
            run_id=run_id,
            sequence=int(current or 0) + 1,
            event_type=event_type,
            payload_json=payload,
            created_at=now,
        )
        s.add(event)
        return event

    def _append_audit(
        self, 
        s: Session,
        run: orm.AgentRun,
        metadata: dict[str, Any],
        now: datetime,
        actor_id: str 
    ) -> None:
        s.add(orm.AuditLog(
            id = f"aud_{uuid.uuid4().hex}",
            user_id = actor_id,
            org_id = run.org_id,
            run_id = run.id,
            action = "run.transition",
            target_type = "agent_run",
            target_id = run.id,
            metadata_json = metadata,
            created_at = now,
        ))

    def append_run_event(
        self, org_id: str, run_id: str, event_type: str, payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Append the next `sequence` for this run. None when the org has no such run."""
        now = utcnow()
        with self.tx() as s:
            owned = s.scalars(
                select(orm.AgentRun.id).where(orm.AgentRun.id == run_id, orm.AgentRun.org_id == org_id)
            ).first()
            if owned is None:
                return None
            event = self._append_event(s, run_id, event_type, payload, now)
            s.flush()
            return _row_dict(event)

    def create_agent_run(
        self,
        org_id: str,
        user_id: str,
        run_type: str,
        idempotency_key: str,
        run_input: dict[str, Any],
        parent_run_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Insert the queued run, `run.created`, and a pending `execute_run` outbox row together.

        False means this org already has the idempotency key, and the returned row is that run. That replay does not add another outbox row.
        """
        now = utcnow()
        run_id = f"run_{uuid.uuid4().hex}"
        try:
            with self.tx() as s:
                run = orm.AgentRun(
                    id=run_id,
                    org_id=org_id,
                    user_id=user_id,
                    run_type=run_type,
                    idempotency_key=idempotency_key,
                    status="queued",
                    input_json=run_input,
                    parent_run_id=parent_run_id,
                    created_at=now,
                    updated_at=now,
                )
                s.add(run)
                s.flush()
                created = {"status": "queued"}
                if parent_run_id is not None:
                    created["parent_run_id"] = parent_run_id
                self._append_event(s, run_id, "run.created", created, now)
                s.add(orm.OutboxJob(
                    id=f"obx_{uuid.uuid4().hex}",
                    job_type="execute_run",
                    payload_json={"run_id": run_id},
                    status="pending",
                    next_attempt_at=now,
                    created_at=now,
                    dispatch_at=now,
                ))
                s.flush()
                return _row_dict(run), True
        except IntegrityError:
            existing = self.find_agent_run_by_idempotency(org_id, idempotency_key)
            if existing is None:
                raise
            return existing, False

    def find_agent_run_by_id(self, run_id: str) -> dict[str, Any] | None:
        """Load a run by primary key. The worker has noorg header; the HTTP API still filters by org."""
        with self.tx() as s:
            row = s.get(orm.AgentRun, run_id)
            return _row_dict(row) if row else None

    def claim_run(self, run_id: str, worker_id: str, lease_expires_at: datetime) -> dict[str, Any] | None:
        """Claim a queued run and append `run.started` together. None when it is no longer queued."""
        now = utcnow()
        with self.tx() as s:
            run = s.scalars(
                select(orm.AgentRun)
                .where(orm.AgentRun.id == run_id, orm.AgentRun.status == "queued")
                .with_for_update()
            ).first()
            if run is None:
                return None
            self._transition(s, run, "running", {"lease_owner": worker_id}, now, lease_owner=worker_id, lease_expires_at=lease_expires_at)
            s.flush()
            return _row_dict(run)

    def claim_chat_turn(self, run_id: str, worker_id: str, lease_expires_at: datetime) -> dict[str, Any] | None:
        """Claim a queued chat turn when that session has no other running run."""
        now = utcnow()
        with self.tx() as s:
            run = s.scalars(
                select(orm.AgentRun)
                .where(orm.AgentRun.id == run_id, orm.AgentRun.status == "queued")
                .with_for_update()
            ).first()
            if run is None:
                return None 
            payload = run.input_json if isinstance(run.input_json, dict) else {}
            session_id = payload.get("session_id")
            if run.run_type == "chat" and isinstance(session_id, str) and session_id:
                s.scalars(
                    select(orm.ChatSession)
                    .where(orm.ChatSession.id == session_id)
                    .with_for_update()
                ).first()
                other = s.scalars(
                    select(orm.AgentRun.id)
                    .where(
                        orm.AgentRun.id != run_id,
                        orm.AgentRun.status == "running",
                        orm.AgentRun.run_type == "chat",
                        orm.AgentRun.input_json["session_id"].as_string() == session_id,
                    )
                    .limit(1)
                ).first()
                if other is not None:
                    raise ChatTurnBusy(run_id)
            job = s.scalars(
                select(orm.JobQueue)
                .where(
                    orm.JobQueue.status == "queued",
                    orm.JobQueue.job_type == "execute_run",
                    orm.JobQueue.payload_json["run_id"].as_string() == run_id,
                )
                .with_for_update()
            ).first()
            if job is not None:
                return None
            job.status = "running"
            job.lock_by = worker_id
            self._transition(
                s, run, "running", {"lease_owner": worker_id}, now, 
                lease_owner=worker_id, lease_expires_at=lease_expires_at
            )
            s.flush()
            return _row_dict(run), str(job.id)

    def sweep_expired_leases(self, limit: int = 100) -> int:
        """Return expired running leases to the queue. This does not spend a retry. 

        A run that already has `cancel_requested_at` is cancelled, and its running 
        queue row is acknowledged. `LeaseLost` acknowledges the queue row and leaves
        the run `running`, so a run with no running queue row gets a new outbox row. 
        """
        now = utcnow()
        with self.tx() as s:
            runs = list(s.scalars(
                select(orm.AgentRun)
                .where(
                    orm.AgentRun.status == "running", 
                    orm.AgentRun.lease_expires_at.is_not(None),
                    orm.AgentRun.lease_expires_at < now
                )
                .order_by(orm.AgentRun.lease_expires_at)
                .limit(limit)
                .with_for_update()
            ))
            for run in runs:
                if run.cancel_requested_at is not None:
                    self._transition(
                        s, run, "cancelled", {"reason": "lease_expired"}, now, 
                        actor_type="sweeper", actor_id="lease-sweep"
                    )
                    self._finish_leased_jobs(s, run.id, now)
                    continue 
                self._transition(
                    s, run, "queued", {"reason": "lease_expired"}, now,
                    actor_type="sweeper", actor_id="lease-sweep"
                )
                if not self._requeue_leased_jobs(s, run.id, now):
                    self._add_execute_outbox(s, run.id, now)
            s.flush()
            return len(runs)

    def _leased_jobs(self, s: Session, run_id: str) -> list[orm.JobQueue]:
        return list(s.scalars(
            select(orm.JobQueue)
            .where(orm.JobQueue.status == "running", orm.JobQueue.payload_json["run_id"].as_string() == run_id)
            .with_for_update()
        ))

    def _requeue_leased_jobs(self, s: Session, run_id: str, now: datetime) -> bool:
        jobs = self._leased_jobs(s, run_id)
        for job in jobs:
            job.status = "queued"
            job.lock_by = None
            job.lock_until = None
            job.available_at = now
            job.last_error_json = {"kind": "lease_expired", "message": "lease expired"}
        return bool(jobs)

    def _finish_leased_jobs(self, s: Session, run_id: str, now: datetime) -> None:
        for job in self._leased_jobs(s, run_id):
            job.status = "succeeded"
            job.finished_at = now

    @staticmethod
    def _add_execute_outbox(s: Session, run_id: str, now: datetime) -> None:
        s.add(orm.OutboxJob(
            id=f"obx_{uuid.uuid4().hex}",
            job_type="execute_run",
            payload_json={"run_id": run_id},
            status="pending",
            next_attempt_at=now,
            created_at=now,
            dispatch_at=now,
        ))

    def complete_run(self, run_id: str, output: dict[str, Any]) -> dict[str, Any] | None:
        """Write `succeeded`, `output_json`, and `run.completed` together. None when the run is not running."""
        now = utcnow()
        with self.tx() as s:
            run = s.scalars(
                select(orm.AgentRun)
                .where(orm.AgentRun.id == run_id, orm.AgentRun.status == "running")
                .with_for_update()
            ).first()
            if run is None:
                return None
            if run.cancel_requested_at is not None:
                return None
            self._transition(s, run, "succeeded", {"status": "succeeded"}, now, output=output)
            s.flush()
            return _row_dict(run)

    def note_running_reply(self, run_id: str, text: str) -> dict[str, Any] | None:
        """Store reply text while the run is still running. The status stays running.
        
        None when the run is missing or not running.
        """
        with self.tx() as s:
            run = s.scalars(
                select(orm.AgentRun)
                .where(orm.AgentRun.id == run_id, orm.AgentRun.status == "running")
                .with_for_update()
            ).first()
            if run is None:
                return None
            run.output_json = {"text": text}
            s.flush()
            return _row_dict(run)

    def mark_waiting_for_user(self, run_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Park a running run and append `run.waiting_for_user`. The actor is the worker.
        
        None when the run is not running, or `cancel_requested_at` is already set. 
        """
        now = utcnow()
        with self.tx() as s:
            run = s.scalars(
                select(orm.AgentRun)
                .where(orm.AgentRun.id == run_id, orm.AgentRun.status == "running")
                .with_for_update()
            ).first()
            if run is None or run.cancel_requested_at is not None:
                return None
            self._transition(s, run, "waiting_for_user", payload or {}, now)
            s.flush()
            return _row_dict(run)

    def run_trace(self, org_id: str, run_id: str) -> dict[str, Any] | None:
        """Run plus its steps, events, model calls, tool calls and checkpoints.

        None when the run is missing or belongs to another org. The lists stay empty until a
        later chapter writes those rows; the final reply is only `output_json`.
        """
        with self.tx() as s:
            run = s.scalars(
                select(orm.AgentRun).where(orm.AgentRun.id == run_id, orm.AgentRun.org_id == org_id)
            ).first()
            if run is None:
                return None
            return {
                "run": _row_dict(run),
                "steps": self._run_rows(s, orm.AgentStep, orm.AgentStep.step_index, run_id),
                "events": self._run_rows(s, orm.RunEvent, orm.RunEvent.sequence, run_id),
                "model_calls": self._run_rows(s, orm.ModelCall, orm.ModelCall.created_at, run_id),
                "tool_calls": self._run_rows(s, orm.ToolCall, orm.ToolCall.created_at, run_id),
                "checkpoints": self._run_rows(s, orm.Checkpoint, orm.Checkpoint.checkpoint_index, run_id),
            }

    @staticmethod
    def _run_rows(s: Session, model: type, order: Any, run_id: str) -> list[dict[str, Any]]:
        rows = s.scalars(select(model).where(model.run_id == run_id).order_by(order))
        return [_row_dict(row) for row in rows]
