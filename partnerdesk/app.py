"""HTTP surface over service.py: sessions, a chat endpoint streamed as SSE, confirm, orders.

    python -m partnerdesk.app            # http://127.0.0.1:8000  (API only — the page is ui.py)

The PO specialist streams its reply token by token; the others answer in one piece.
Confirmation is its own endpoint — the model is never on that path.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from functools import lru_cache
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import service
from .db import Database
from .llm import LLM, OpenAICompatLLM


# Module-level on purpose: with `from __future__ import annotations` FastAPI resolves the
# string annotation against module globals, and a class defined inside `create_app` would
# silently become a query parameter.
class NewSession(BaseModel):
    partnership_id: str | None = None


class Inbound(BaseModel):
    message: str = ""
    images: list[str] = []       # data URLs — read by the PO extractor
    attachments: list[str] = []  # file names — seen by the router only
    model: str | None = None     # campus call name; default is LLM_MODEL


class Confirm(BaseModel):
    token: str


def create_app(db: Database | None = None, llm: LLM | None = None) -> FastAPI:
    app = FastAPI(title="partnerdesk")
    state: dict[str, Any] = {"db": db, "llm": llm}

    def get_db() -> Database:
        if state["db"] is None:
            state["db"] = Database()
            state["db"].seed_from_fixtures()
        return state["db"]

    def get_llm() -> LLM:
        if state["llm"] is None:
            state["llm"] = OpenAICompatLLM()
        return state["llm"]

    @app.get("/")
    def index() -> dict[str, str]:
        return {"service": "partnerdesk", "ui": "streamlit run partnerdesk/ui.py", "docs": "/docs"}

    @app.get("/api/partnerships")
    def partnerships() -> list[dict[str, Any]]:
        return [p.model_dump() for p in get_db().partnerships()]

    @app.post("/api/sessions")
    def new_session(body: NewSession) -> dict[str, Any]:
        try:
            return service.new_session(get_db(), body.partnership_id)
        except KeyError as err:
            raise HTTPException(404, str(err)) from err

    @app.get("/api/sessions/{sid}")
    def get_session(sid: str) -> dict[str, Any]:
        s = get_db().session(sid)
        if not s:
            raise HTTPException(404, "session not found")
        return s

    @app.post("/api/sessions/{sid}/message")
    def message(sid: str, body: Inbound) -> StreamingResponse:
        d, llm = get_db(), get_llm()
        if body.model and isinstance(llm, OpenAICompatLLM):
            llm = llm.with_model(body.model)
        if not d.session(sid):
            raise HTTPException(404, "session not found")
        if not body.message.strip() and not body.images:
            raise HTTPException(400, "message required")

        def gen() -> Iterator[bytes]:
            for e in service.handle_message(d, llm, sid, body.message, body.images, body.attachments):
                yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n".encode()

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/sessions/{sid}/confirm")
    def confirm(sid: str, body: Confirm) -> dict[str, Any]:
        try:
            return service.confirm(get_db(), sid, body.token)
        except KeyError as err:
            raise HTTPException(404, str(err)) from err

    @app.get("/api/orders")
    def orders(partnership_id: str | None = None) -> list[dict[str, Any]]:
        return service.orders(get_db(), partnership_id)

    return app


@lru_cache(maxsize=1)
def default_app() -> FastAPI:
    return create_app()


app = default_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("partnerdesk.app:app", host="127.0.0.1", port=8000, reload=False)
