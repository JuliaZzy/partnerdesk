"""HTTP endpoints for the exercise Run API.

Each function reads the request, calls run_service, and returns the response.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Body, Depends, Header, Response

from ..db import Database
from ..deps import get_db
from ..schemas.run import (
    CancelRunRequest,
    CreateRunRequest,
    RerunRunRequest,
    ResumeRunRequest,
    RunEventResponse,
    RunResponse,
    TraceResponse,
)
from ..services import run_service

router = APIRouter()


@dataclass
class CurrentUser:
    org_id: str
    id: str


def get_current_user(
    x_org_id: str = Header(alias="X-Org-Id"),
    x_user_id: str = Header(alias="X-User-Id"),
) -> CurrentUser:
    return CurrentUser(org_id=x_org_id, id=x_user_id)


@router.post("/runs", response_model=RunResponse, status_code=201)
def create_run(
    request: CreateRunRequest,
    response: Response,
    user: CurrentUser = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> dict:
    run, created = run_service.create_run(
        db,
        org_id=user.org_id,
        user_id=user.id,
        run_type=request.run_type,
        idempotency_key=request.idempotency_key,
        run_input=request.input,
    )
    if not created:
        response.status_code = 200
    return run


@router.post("/runs/{run_id}/rerun", response_model=RunResponse, status_code=201)
def rerun_run(
    run_id: str,
    request: RerunRunRequest,
    response: Response,
    user: CurrentUser = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> dict:
    run, created = run_service.rerun(
        db,
        org_id=user.org_id,
        user_id=user.id,
        run_id=run_id,
        idempotency_key=request.idempotency_key,
        run_input=request.input,
    )
    if not created:
        response.status_code = 200
    return run


@router.get("/runs/{run_id}", response_model=RunResponse)
def get_run(run_id: str, user: CurrentUser = Depends(get_current_user), db: Database = Depends(get_db)) -> dict:
    return run_service.get_run(db, user.org_id, run_id)


@router.get("/runs/{run_id}/events", response_model=list[RunEventResponse])
def get_run_events(
    run_id: str, user: CurrentUser = Depends(get_current_user), db: Database = Depends(get_db),
) -> list:
    return run_service.list_events(db, user.org_id, run_id)


@router.post("/runs/{run_id}/cancel", response_model=RunResponse)
def cancel_run(
    run_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Database = Depends(get_db),
    request: CancelRunRequest | None = Body(default=None),
) -> dict:
    reason = None if request is None else request.reason
    return run_service.cancel_run(db, user.org_id, run_id, reason)


@router.post("/runs/{run_id}/resume", response_model=RunResponse)
def resume_run(
    run_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Database = Depends(get_db),
    request: ResumeRunRequest | None = Body(default=None),
) -> dict:
    run_input = None if request is None else request.input
    return run_service.resume_run(db, user.org_id, run_id, run_input)


@router.get("/runs/{run_id}/trace", response_model=TraceResponse)
def get_run_trace(
    run_id: str, user: CurrentUser = Depends(get_current_user), db: Database = Depends(get_db),
) -> dict:
    return run_service.get_trace(db, user.org_id, run_id)
