"""HTTP entry. Routers live in `router/`; this module only builds the app and hands it a
database and a model for this process (tests pass their own).

    ./dev api                            # http://127.0.0.1:8000/docs
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from .db import Database
from .router import include_routers


def create_app(db: Database | None = None, llm: Any | None = None) -> FastAPI:
    app = FastAPI(title="KA Account Agent")
    app.state.db = db
    app.state.llm = llm
    include_routers(app)
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.app:app", host="0.0.0.0", port=8000, reload=False)
