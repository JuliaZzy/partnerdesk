"""A standing note about a partnership, plus the two bodies the memory page posts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class Memory(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    partnership_id: str
    user_id: str | None = None
    kind: Literal["preference", "fact", "instruction", "event"] = "fact"
    content: str
    source: Literal["user", "chat", "system"] = "user"
    session_id: str | None = None
    is_active: bool = True
    last_recalled_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MemoryIn(BaseModel):
    content: str
    kind: str = "fact"
    user_id: str | None = None


class MemoryActive(BaseModel):
    active: bool
