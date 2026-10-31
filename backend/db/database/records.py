"""Steps, checkpoints, model calls, and tool calls for one harness iteration. 

Each method is its own transaction. The run row is locked while the next 
step or checkpoint index is chosen. Event names match `StepEvent` in
`COMPLETED_TOOL_CALLS_KEY`, and `LAST_KEY` there.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError

from .. import orm
from .support import _row_dict, utcnow

_STEP_TYPES = frozenset({"model", "tool"})
_DONE = frozenset({"succeeded", "failed"})
_CHECKPOINT_KEYS = frozenset({"context", "completed_tool_calls", "last"})

class RecordMixin:
    def latest_checkpoint(self, run_id: str) -> dict[str, Any] | None:
        with self.tx() as s:
            row = s.scalars(
                select(orm.Checkpoint)
                .where(orm.Checkpoint.run_id == run_id)
                .order_by(orm.Checkpoint.checkpoint_index.desc())
                .limit(1)
            ).first()
            return _row_dict(row) if row else None

    def append_checkpoint(self, run_id: str, state: dict[str, Any]) -> int:
        self._require_checkpoint_state(state)
        now = utcnow()
        with self.tx() as s:
            run = self._lock_run(s, run_id)
            if run is None:
                return None
            current = s.scalar(
                select(func.max(orm.Checkpoint.checkpoint_index))
                .where(orm.Checkpoint.run_id == run_id)
            )
            row = orm.Checkpoint(
                id=f"ckpt_{uuid.uuid4().hex}",
                run_id=run_id,
                checkpoint_index = int(current or 0) + 1,
                state_json=state,
                created_at=now,
            )
            s.add(row)
            s.flush()
            return _row_dict(row)

    def insert_step(
        self, 
        run_id: str, 
        step_type: str, 
        name: str, 
        step_input: dict[str, Any]
    ) -> dict[str, Any]:
        """Open one iteration as `running` and append `step_started`.

        None when the run is missing or is not `running`. The caller picks
        `name` with `step_name` before this write. 
        """
        if step_type not in _STEP_TYPES:
            raise ValueError(f"Invalid step type: {step_type}")
        if not isinstance(name, str) or not name:
            raise ValueError("`name` is required")
        now = utcnow()
        with self.tx() as s:
            run = self._lock_run(s, run_id)
            if run is None or run.status != "running":
                return None
            current = s.scalar(
                select(func.max(orm.AgentStep.step_index))
                .where(orm.AgentStep.run_id == run_id)
            )
            row = orm.AgentStep(
                id=f"step_{uuid.uuid4().hex}",
                run_id=run_id,
                status="running",
                step_index=int(current or 0) + 1,
                step_type=step_type,
                name=name,
                input_json=step_input,
                started_at=now,
            )
            s.add(row)
            s.flush()
            self._append_event(s, run_id, "step_started", {
                "step_id": row.id, "step_index": row.step_index, "step_type": row.step_type, "name": name
            }, now)
            s.flush()
            return _row_dict(row)

    def finish_step(
        self, 
        step_id: str,
        status: str,
        output: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Close a running step. A second call returns the row and does not append again."""
        if status not in _DONE:
            raise ValueError(f"Invalid step status: {status}")
        now = utcnow()
        with self.tx() as s:
            step = s.scalars(
                select(orm.AgentStep)
                .where(orm.AgentStep.id == step_id)
                .with_for_update()
            ).first()
            if step is None:
                return None
            if step.status != "running":
                return _row_dict(step)
            step.status = status
            step.output_json = output
            step.error_json = error
            step.ended_at = now
            event = "step_failed" if status == "failed" else "step_completed"
            self._append_event(s, step.run_id, event, {
                "step_id": step_id, "step_index": step.step_index, "status": status
            }, now)
            s.flush()
            return _row_dict(step)

    def insert_model_call(
        self,
        run_id: str,
        step_id: str,
        *, 
        provider: str,
        model: str,
        request: dict[str, Any],
        response: dict[str, Any],
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: int,
        status: str,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record one finished model call. None when the step is not on this run. 
        
        The row is written after the call returns, so `created_at` is the write
        time and `latency_ms` is the measured duration.
        """
        if status not in _DONE:
            raise ValueError("model call status must be succeeded or failed")
        now = utcnow()
        prompt_tokens = max(0, int(prompt_tokens))
        completion_tokens = max(0, int(completion_tokens))
        with self.tx() as s:
            step = self._lock_step(s, run_id, step_id)
            if step is None:
                return None
            run = s.get(orm.AgentRun, run_id)
            if run is None:
                return None
            row = orm.ModelCall(
                id = f"mcall_{uuid.uuid4().hex}",
                run_id = run_id,
                step_id = step_id,
                provider = provider,
                model = model,
                request_json = request,
                response_json = response or {},
                prompt_tokens = prompt_tokens,
                completion_tokens = completion_tokens,
                latency_ms = latency_ms,
                status = status,
                error_json = error or {},
                created_at = now,
            )
            s.add(orm.UsageRecord(
                id=f"usage_{uuid.uuid4().hex}",
                run_id=run_id,
                org_id=run.org_id,
                user_id=run.user_id,
                provider=provider,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                estimated_cost_cents=0,
                created_at=now,
            ))
            s.add(row)
            s.flush()
            event = "model_failed" if status == "failed" else "model_completed"
            self._append_event(s, run_id, event, {
                "step_id": step_id, "model_call_id": row.id, "status": status
            }, now)
            s.flush()
            return _row_dict(row)

    def insert_tool_call(
        self,
        run_id: str,
        step_id: str,
        *, 
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any],
        status: str,
        idempotency_key: str, 
        latency_ms: int,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Record one finished tool call. 

        A replay does not append another event. None when the step is not on this run.
        """
        if status not in _DONE:
            raise ValueError("tool call status must be succeeded or failed")
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise ValueError("`idempotency_key` is required")
        now = utcnow()
        try:
            with self.tx() as s:
                step = self._lock_step(s, run_id, step_id)
                if step is None:
                    return None
                existing = s.scalars(
                    select(orm.ToolCall)
                    .where(
                        orm.ToolCall.tool_name == tool_name, 
                        orm.ToolCall.idempotency_key == idempotency_key
                    )
                ).first()
                if existing is not None:
                    return _row_dict(existing)
                row = orm.ToolCall(
                    id = f"tcall_{uuid.uuid4().hex}",
                    run_id = run_id,
                    step_id = step_id,
                    tool_name = tool_name,
                    args_json = args,
                    result_json = result or {},
                    status = status,
                    idempotency_key = idempotency_key,
                    latency_ms = latency_ms,
                    error_json = error or {},
                    created_at = now,
                    ended_at = now,
                )
                s.add(row)
                s.flush()
                event = "tool_failed" if status == "failed" else "tool_completed"
                self._append_event(s, run_id, event, {
                    "step_id": step_id, "tool_call_id": row.id, "tool_name": tool_name, "status": status
                }, now)
                s.flush()
                return _row_dict(row)
        except IntegrityError:
            with self.tx() as s:
                existing = s.scalars(
                    select(orm.ToolCall)
                    .where(
                        orm.ToolCall.tool_name == tool_name, 
                        orm.ToolCall.idempotency_key == idempotency_key
                    )
                ).first()
                if existing is None:
                    raise
                return _row_dict(existing)

    def claim_tool_call(
        self,
        run_id: str,
        step_id: str,
        *, 
        tool_name: str,
        args: dict[str, Any],
        idempotency_key: str,
    ) -> tuple[dict[str, Any], bool] | None:
        """Reserve one side effect before it runs.

        The bool is True when this call inserted the row.
        False means the key already exists, so the caller must not run the tool again. 
        None when the step is not on this run.
        """
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise ValueError("`idempotency_key` is required")
        now = utcnow()
        try:
            with self.tx() as s:
                step = self._lock_step(s, run_id, step_id)
                if step is None:
                    return None
                existing = s.scalars(
                    select(orm.ToolCall)
                    .where(orm.ToolCall.tool_name == tool_name, orm.ToolCall.idempotency_key == idempotency_key)
                ).first()
                if existing is not None:
                    return _row_dict(existing), False
                row = orm.ToolCall(
                    id = f"tcall_{uuid.uuid4().hex}",
                    run_id = run_id,
                    step_id = step_id,
                    tool_name = tool_name,
                    args_json = args,
                    result_json = {},
                    status = "running", 
                    idempotency_key = idempotency_key,
                    latency_ms = 0,
                    created_at = now,
                )
                s.add(row)
                s.flush()
                return _row_dict(row), True
        except IntegrityError:
            with self.tx() as s:
                existing = s.scalars(
                    select(orm.ToolCall)
                    .where(orm.ToolCall.tool_name == tool_name, orm.ToolCall.idempotency_key == idempotency_key)
                ).first()
                if existing is None:
                    raise
                return _row_dict(existing), False

    def complete_tool_call(
        self, 
        tool_call_id: str,
        *,
        result: dict[str, Any],
        status: str,
        latency_ms: int,
        error: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Fill a claimed tool call. A second call returns the row and does not append again."""
        if status not in _DONE:
            raise ValueError("tool call status must be succeeded or failed")
        now = utcnow()
        with self.tx() as s:
            row = s.scalars(
                select(orm.ToolCall)
                .where(orm.ToolCall.id == tool_call_id)
                .with_for_update()
            ).first()
            if row is None:
                return None
            if row.status != "running":
                return _row_dict(row)
            row.result_json = result or {}
            row.status = status
            row.latency_ms = latency_ms
            row.error_json = error or {}
            row.ended_at = now
            event = "tool_failed" if status == "failed" else "tool_completed"
            self._append_event(s, row.run_id, event, {
                "step_id": row.step_id, "tool_call_id": row.id, "tool_name": row.tool_name, "status": status
            }, now)
            s.flush()
            return _row_dict(row)

    @staticmethod
    def _require_checkpoint_state(state: dict[str, Any]) -> None:
        missing = [key for key in _CHECKPOINT_KEYS if key not in state]
        if missing:
            raise ValueError("checkpoint state needs context, completed_tool_calls, and last")
        if not isinstance(state["context"], list) or not isinstance(state["completed_tool_calls"], list):
            raise ValueError("checkpoint context and completed_tool_calls must be lists")
        if not isinstance(state["last"], str):
            raise ValueError("checkpoint last must be a string")
        if "last_report" in state and not isinstance(state["last_report"], str):
            raise ValueError("checkpoint last_report must be a string")

    @staticmethod
    def _lock_run(s, run_id: str) -> orm.AgentRun | None:
        return s.scalars(
            select(orm.AgentRun)
            .where(orm.AgentRun.id == run_id)
            .with_for_update()
        ).first()

    def list_usage_records(self, run_id: str) -> list[dict[str, Any]]:
        """Usage rows for one run, oldest first."""
        with self.tx() as s:
            rows = s.scalars(
                select(orm.UsageRecord)
                .where(orm.UsageRecord.run_id == run_id)
                .order_by(orm.UsageRecord.created_at, orm.UsageRecord.id)
            )
            return [_row_dict(row) for row in rows]

    @staticmethod
    def _lock_step(s, run_id: str, step_id: str) -> orm.AgentStep | None:
        return s.scalars(
            select(orm.AgentStep)
            .where(orm.AgentStep.id == step_id, orm.AgentStep.run_id == run_id)
            .with_for_update()
        ).first()