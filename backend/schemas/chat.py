"""Bodies for the chat endpoints.

Defined at module level and imported by the session router. With
`from __future__ import annotations`, FastAPI resolves a body annotation against the
endpoint's module globals; a class created inside `create_app` would be read as a query
parameter.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class NewSession(BaseModel):
    partnership_id: str | None = None


class InboundFile(BaseModel):
    name: str
    data: str  # data URL (data:<mime>;base64,...) or raw base64


class Inbound(BaseModel):
    message: str = ""
    images: list[str] = []       # data URLs — read by the PO extractor
    attachments: list[str] = []  # file names — seen by the orchestrator only
    files: list[InboundFile] = []  # the document bytes, for the contract specialist
    model: str | None = None     # dropdown id from llm_config; omitted uses the first row


class Confirm(BaseModel):
    token: str


class DataFeedback(BaseModel):
    log_id: int
    feedback: Literal["good", "bad"]
    reason: str | None = None
    corrected_sql: str | None = None
