"""HTTP routers, one module per desk area. `app.py` includes them."""

from __future__ import annotations

from fastapi import FastAPI

from .catalog_router import router as catalog_router
from .contract_router import router as contract_router
from .memory_router import router as memory_router
from .meta_router import router as meta_router
from .order_router import router as order_router
from .report_router import router as report_router
from .session_router import router as session_router


def include_routers(app: FastAPI) -> None:
    for item in (
        meta_router, session_router, catalog_router, contract_router, report_router, memory_router, order_router,
    ):
        app.include_router(item)
