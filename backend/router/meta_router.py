"""Desk-wide reads: service index, model list, partnerships."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from .. import service
from ..config import chat_models, default_model_id
from ..db import Database
from ..deps import get_db
from ..memory import KIND_LABELS
from ..schemas import PRODUCT_STATUS_VALUES

router = APIRouter()


@router.get("/")
def index() -> dict[str, str]:
    return {"service": "ka-account-agent", "ui": "http://localhost:5173", "docs": "/docs"}


@router.get("/api/meta")
def meta() -> dict[str, Any]:
    return {
        "models": [
            {
                "id": m.id, "label": m.label, "short": m.short or m.label, "description": m.description,
                "vision": m.vision, "thinking": m.thinking,
            }
            for m in chat_models()
        ],
        "default_model": default_model_id(),
        "product_statuses": list(PRODUCT_STATUS_VALUES),
        "memory_kinds": KIND_LABELS,
        "max_images": service.MAX_IMAGES,
    }


@router.get("/api/partnerships")
def partnerships(db: Database = Depends(get_db)) -> list[dict[str, Any]]:
    return [p.model_dump() for p in db.partnerships()]
