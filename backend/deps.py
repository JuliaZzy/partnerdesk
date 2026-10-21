"""Per-app database and model. `create_app` stores them on `app.state`; routers read them
back through these dependencies, so two tests do not share one connection or one fake model.
"""

from __future__ import annotations

from fastapi import Request

from .db import Database
from .llm import LLM, OpenAICompatLLM


def get_db(request: Request) -> Database:
    db = request.app.state.db
    if db is None:
        db = Database()
        request.app.state.db = db
    return db


def get_llm(request: Request) -> LLM:
    llm = request.app.state.llm
    if llm is None:
        llm = OpenAICompatLLM()
        request.app.state.llm = llm
    return llm


def llm_for(llm: LLM, model: str | None) -> LLM:
    """A client this request owns.

    The app keeps one OpenAI HTTP client. Concurrent chats must not share its
    `model_outputs` list, or one turn's JSON is recorded on the other.
    """
    if isinstance(llm, OpenAICompatLLM):
        owned = OpenAICompatLLM(settings=llm.settings, _client=llm._client, model_outputs=[])
        if model:
            return owned.with_model(model)
        return owned
    return llm
