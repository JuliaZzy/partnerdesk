"""Submitted orders for one partnership, or every partnership."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from .. import service
from ..db import Database
from ..deps import get_db

router = APIRouter()


@router.get("/api/orders")
def orders(partnership_id: str | None = None, db: Database = Depends(get_db)) -> list[dict[str, Any]]:
    return service.orders(db, partnership_id)
