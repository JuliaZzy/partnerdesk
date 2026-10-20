"""One uploaded brand file. Page text lives on the row, not in this object."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class BrandDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    brand_id: str
    title: str | None = None
    file_name: str
    file_sha256: str | None = None
    media_type: str | None = None
    size_bytes: int | None = None
    page_count: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
