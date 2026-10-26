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


def user_on_partnership(db: Database, partnership: Partnership, user_id: str | None) -> None:
    """A person may only open chats and memories for their own distributor. Omit the id to skip."""
    if not user_id:
        return
    person = db.user(user_id)
    if person is None or person.distributor_id != partnership.distributor_id:
        raise HTTPException(400, "user is not on this distributor")


async def read_upload(file: UploadFile) -> tuple[str, bytes]:
    name = file.filename or "file"
    data = await file.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(400, f"{name} is larger than 12 MB")
    if not data:
        raise HTTPException(400, "empty file")
    return name, data
