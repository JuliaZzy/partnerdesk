"""A person who uses the desk for one distributor. No password — the sidebar switches them."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class User(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    distributor_id: str
    name: str
