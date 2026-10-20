"""Helpers shared by the desk routers."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, UploadFile

from ..db import Database
from ..schemas import Partnership

_MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def dump(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj


def partnership_or_404(db: Database, pid: str) -> Partnership:
    found = db.partnership(pid)
    if not found:
        raise HTTPException(404, "partnership not found")
    return found


async def read_upload(file: UploadFile) -> tuple[str, bytes]:
    name = file.filename or "file"
    data = await file.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(400, f"{name} is larger than 12 MB")
    if not data:
        raise HTTPException(400, "empty file")
    return name, data
