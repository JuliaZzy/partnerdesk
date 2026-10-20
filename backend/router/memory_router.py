"""Read and edit the standing notes for a partnership."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from ..db import Database
from ..deps import get_db
from ..memory import KIND_LABELS, memory_block, remember
from ..schemas import MemoryActive, MemoryIn
from ._common import dump, partnership_or_404

router = APIRouter()


@router.get("/api/partnerships/{pid}/memories")
def memories(pid: str, db: Database = Depends(get_db)) -> dict[str, Any]:
    partnership_or_404(db, pid)
    active = db.memories(pid, active_only=True)
    inactive = [m for m in db.memories(pid, active_only=False) if not m.is_active]
    return {
        "active": [dump(m) for m in active],
        "inactive": [dump(m) for m in inactive],
        "block": memory_block(active),
    }


@router.post("/api/partnerships/{pid}/memories")
def add_memory(pid: str, body: MemoryIn, db: Database = Depends(get_db)) -> dict[str, Any]:
    partnership_or_404(db, pid)
    if body.kind not in KIND_LABELS:
        raise HTTPException(400, "unknown memory kind")
    try:
        saved = remember(db, pid, body.content, kind=body.kind, source="user")
    except ValueError as err:
        raise HTTPException(400, str(err)) from err
    return dump(saved)


@router.patch("/api/memories/{mid}")
def set_memory(mid: int, body: MemoryActive, db: Database = Depends(get_db)) -> dict[str, Any]:
    db.set_memory_active(mid, body.active)
    return {"id": mid, "active": body.active}


@router.delete("/api/memories/{mid}", status_code=204)
def delete_memory(mid: int, db: Database = Depends(get_db)) -> Response:
    db.delete_memory(mid)
    return Response(status_code=204)
