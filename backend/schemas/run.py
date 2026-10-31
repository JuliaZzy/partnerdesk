"""Request and response bodies for the exercise Run API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CreateRunRequest(BaseModel):
    run_type: str
    idempotency_key: str
    input: dict[str, Any]


class RerunRunRequest(BaseModel):
    idempotency_key: str
    input: dict[str, Any] | None = None

class RunResponse(BaseModel):
    id: str
    org_id: str
    user_id: str
    run_type: str
    idempotency_key: str
    status: str
    input_json: dict[str, Any]
    output_json: dict[str, Any] | None = None
    error_json: dict[str, Any] | None = None
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    updated_at: datetime
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    attempts: int
    parent_run_id: str | None = None


class RunEventResponse(BaseModel):
    id: str
    run_id: str
    sequence: int
    event_type: str
    payload_json: dict[str, Any]
    created_at: datetime


class CancelRunRequest(BaseModel):
    reason: str | None = None


class ResumeRunRequest(BaseModel):
    resume_token: str | None = None
    input: dict[str, Any] | None = None


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class AgentRun(BaseModel):
    id: str
    org_id: str
    user_id: str
    run_type: str
    idempotency_key: str
    status: str
    input_json: dict[str, Any]
    output_json: dict[str, Any] | None = None
    error_json: dict[str, Any] | None = None
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    updated_at: datetime
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    attempts: int
    parent_run_id: str | None = None


class AgentStep(BaseModel):
    id: str
    run_id: str
    step_index: int
    step_type: str
    name: str
    status: str
    input_json: dict[str, Any]
    output_json: dict[str, Any] | None = None
    error_json: dict[str, Any] | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None


class RunEvent(BaseModel):
    id: str
    run_id: str
    sequence: int
    event_type: str
    payload_json: dict[str, Any]
    created_at: datetime


class Checkpoint(BaseModel):
    id: str
    run_id: str
    checkpoint_index: int
    state_json: dict[str, Any]
    created_at: datetime


class UsageRecord(BaseModel):
    id: str
    run_id: str
    org_id: str
    user_id: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_cents: int
    created_at: datetime


class ModelCall(BaseModel):
    id: str
    run_id: str
    step_id: str
    provider: str
    model: str
    request_json: dict[str, Any]
    response_json: dict[str, Any]
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    status: str
    error_json: dict[str, Any] | None = None
    created_at: datetime


class ToolCall(BaseModel):
    id: str
    run_id: str
    step_id: str
    tool_name: str
    args_json: dict[str, Any]
    result_json: dict[str, Any]
    status: str
    idempotency_key: str
    latency_ms: int
    error_json: dict[str, Any] | None = None
    created_at: datetime
    ended_at: datetime | None = None


class AuditLog(BaseModel):
    id: str
    user_id: str
    org_id: str
    run_id: str
    action: str
    target_type: str
    target_id: str
    metadata_json: dict[str, Any]
    created_at: datetime


class OutboxJob(BaseModel):
    id: str
    job_type: str
    payload_json: dict[str, Any]
    status: str
    attempts: int
    max_attempts: int
    next_attempt_at: datetime
    last_error_json: dict[str, Any] | None = None
    created_at: datetime
    dispatch_at: datetime


class DeadLetterJob(BaseModel):
    id: str
    original_job_id: str
    job_type: str
    payload_json: dict[str, Any]
    attempts: int
    error_json: dict[str, Any]
    created_at: datetime


class JobQueue(BaseModel):
    id: str
    job_type: str
    payload_json: dict[str, Any]
    status: str
    attempts: int
    max_attempts: int
    available_at: datetime
    lock_by: str | None = None
    lock_until: datetime | None = None
    last_error_json: dict[str, Any] | None = None
    created_at: datetime
    finished_at: datetime | None = None


class TraceResponse(BaseModel):
    run: RunResponse
    steps: list[AgentStep] = Field(default_factory=list)
    events: list[RunEventResponse]
    model_calls: list[ModelCall] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    checkpoints: list[Checkpoint] = Field(default_factory=list)
