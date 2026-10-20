"""HTTP entry. Routers live in `router/`; this module only builds the app and hands it a
database and a model for this process (tests pass their own).

    ./dev api                            # http://127.0.0.1:8000/docs
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import FastAPI

from .db import Database
from .llm import LLM
from .router import include_routers


def create_app(db: Database | None = None, llm: LLM | None = None) -> FastAPI:
    app = FastAPI(title="partnerdesk")
    app.state.db = db
    app.state.llm = llm
    include_routers(app)
    return app


@lru_cache(maxsize=1)
def default_app() -> FastAPI:
    return create_app()


app = default_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.app:app", host="0.0.0.0", port=8000, reload=False)
