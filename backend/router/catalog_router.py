"""Product catalog search for the desk page."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..db import Database
from ..deps import get_db

router = APIRouter()


@router.get("/api/catalog/categories")
def catalog_categories(db: Database = Depends(get_db)) -> list[str]:
    return db.catalog_categories()


@router.get("/api/catalog")
def catalog(
    q: str | None = None, brand: str | None = None, category: str | None = None, status: str | None = None,
    page: int = 1, page_size: int = 100, db: Database = Depends(get_db),
) -> dict[str, Any]:
    page_size = min(max(page_size, 1), 200)
    page = max(page, 1)
    stats = db.catalog_stats(q, brand=brand, category=category, status=status)
    items, total = db.search_catalog(
        q, brand=brand, category=category, status=status, limit=page_size, offset=(page - 1) * page_size,
    )
    return {"items": items, "total": total, "stats": stats, "page": page, "page_size": page_size}
