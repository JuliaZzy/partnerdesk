"""Exercise Run API. Separate from the desk app in backend.app."""

from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .db import Database
from .routes.runs import router
from .services.run_service import AppError


def _request_id(request: Request) -> str:
    existing = getattr(request.state, "request_id", None)
    if isinstance(existing, str) and existing:
        return existing
    request_id = request.headers.get("X-Request-Id") or f"req_{uuid4().hex[:12]}"
    request.state.request_id = request_id
    return request_id


def _error_body(request: Request, code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message, "request_id": _request_id(request)}}


def create_app(db: Database | None = None) -> FastAPI:
    app = FastAPI(title="Run API")
    app.state.db = db
    app.include_router(router)

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        _request_id(request)
        return await call_next(request)

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=_error_body(request, exc.code, exc.message))

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, _exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_error_body(request, "VALIDATION_ERROR", "Request validation failed"),
        )

    return app


app = create_app()
