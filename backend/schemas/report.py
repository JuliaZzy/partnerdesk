"""A sales report and one fact read out of it."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class SalesReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    brand_id: str
    partnership_id: str
    title: str | None = None
    file_name: str
    file_sha256: str | None = None
    media_type: str | None = None
    size_bytes: int | None = None
    reporting_period: str | None = None  # YYYY-MM | YYYY-Qn | YYYY
    period_start: str | None = None
    period_end: str | None = None
    status: Literal["draft", "confirmed"] = "draft"
    confirmed_at: datetime | None = None
    notes: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ReportFact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    report_id: str | None = None
    extraction_id: str | None = None
    metric_key: str
    fact_type: str
    channel: str | None = None
    sku: str | None = None
    product_id: str | None = None
    entity: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    numeric_value: float | None = None
    text_value: str | None = None
    unit: str | None = None
    currency: str | None = None
    source_sheet: str | None = None
    source_row: int | None = None
    source_col: int | None = None
    source_header: str | None = None
    ai_derived: bool = False
    status: Literal["draft", "confirmed", "superseded"] = "draft"
    created_at: datetime | None = None

    def identity(self) -> tuple[Any, ...]:
        """What makes two facts 'the same measurement' — a re-import supersedes on this key."""
        return (self.metric_key, self.fact_type, self.channel, self.sku, self.entity, self.period_start, self.period_end)


class NarrativeReport(BaseModel):
    """A chat write-up the user edited and confirmed. Numbers stay in `report_facts`."""

    model_config = ConfigDict(extra="ignore")

    id: str
    brand_id: str
    partnership_id: str
    session_id: str | None = None
    title: str
    body: str
    charts: list[dict[str, Any]] = []
    status: Literal["confirmed"] = "confirmed"
    confirmed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
