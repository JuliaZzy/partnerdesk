"""Per-app database and model. `create_app` stores them on `app.state`; routers read them
back through these dependencies, so two tests do not share one connection or one fake model.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from .db import Database


def get_db(request: Request) -> Database:
    db = request.app.state.db
    if db is None:
        db = Database()
        request.app.state.db = db
    return db


def get_llm(request: Request) -> Any:
    """The object tests pass in. A running desk does not use it to choose a model."""
    return getattr(request.app.state, "llm", None)
