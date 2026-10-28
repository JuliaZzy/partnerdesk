"""Tables for `schemas/report.py`.

A distributor's sell-out / inventory report: one row per file, each run of a reader
over it kept in `report_extractions` (how the numbers were located), and the numbers
themselves as facts at native granularity. Facts are draft until a person confirms the
report; a later report for the same period supersedes the earlier facts, never doubles them.

A narrative report is the prose composed in chat. It stays out of the database until the
user confirms the text they actually want kept.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, JSONCol, TimestampCol


class SalesReport(Base):
    __tablename__ = "sales_reports"
    __table_args__ = (Index("ix_sales_reports_partnership_period", "partnership_id", "period_start"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    partnership_id: Mapped[str] = mapped_column(ForeignKey("partnerships.id"))
    title: Mapped[str | None] = mapped_column(String(200))
    file_name: Mapped[str] = mapped_column(String(255))
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    media_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    reporting_period: Mapped[str | None] = mapped_column(String(10))  # YYYY-MM | YYYY-Qn | YYYY
    period_start: Mapped[str | None] = mapped_column(String(10))
    period_end: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | confirmed
    confirmed_at: Mapped[datetime | None] = mapped_column(TimestampCol)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TimestampCol)
    updated_at: Mapped[datetime] = mapped_column(TimestampCol)


class ReportExtraction(Base):
    """One run of a reader over the report — provenance for every fact it produced."""

    __tablename__ = "report_extractions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("sales_reports.id", ondelete="CASCADE"), index=True)
    method: Mapped[str] = mapped_column(String(32))  # tidy_table | ai_table_extractor | manual
    model: Mapped[str | None] = mapped_column(String(100))
    raw: Mapped[dict[str, Any]] = mapped_column(JSONCol, default=dict)  # the column mapping / tables located
    skipped: Mapped[list[str]] = mapped_column(JSONCol, default=list)
    extracted_at: Mapped[datetime] = mapped_column(TimestampCol)


class ReportFact(Base):
    __tablename__ = "report_facts"
    __table_args__ = (
        Index("ix_report_facts_report_status", "report_id", "status"),
        Index("ix_report_facts_sku_period", "sku", "period_start"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("sales_reports.id", ondelete="CASCADE"), index=True)
    extraction_id: Mapped[str | None] = mapped_column(ForeignKey("report_extractions.id", ondelete="SET NULL"))
    metric_key: Mapped[str] = mapped_column(String(64))  # revenue | units_sold | stock_on_hand | … (canonical)
    fact_type: Mapped[str] = mapped_column(String(32))  # sales | inventory | marketing | narrative | …
    channel: Mapped[str | None] = mapped_column(String(100))
    sku: Mapped[str | None] = mapped_column(String(64))
    product_id: Mapped[str | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), index=True)
    entity: Mapped[str | None] = mapped_column(String(200))
    period_start: Mapped[str | None] = mapped_column(String(10))
    period_end: Mapped[str | None] = mapped_column(String(10))
    numeric_value: Mapped[float | None] = mapped_column(Float)
    text_value: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(32))
    currency: Mapped[str | None] = mapped_column(String(8))
    source_sheet: Mapped[str | None] = mapped_column(String(100))
    source_row: Mapped[int | None] = mapped_column(Integer)
    source_col: Mapped[int | None] = mapped_column(Integer)
    source_header: Mapped[str | None] = mapped_column(String(200))  # the column header verbatim
    ai_derived: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | confirmed | superseded
    created_at: Mapped[datetime] = mapped_column(TimestampCol)


class NarrativeReport(Base):
    __tablename__ = "narrative_reports"
    __table_args__ = (Index("ix_narrative_reports_partnership_updated", "partnership_id", "updated_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    partnership_id: Mapped[str] = mapped_column(ForeignKey("partnerships.id"))
    session_id: Mapped[str | None] = mapped_column(ForeignKey("sessions.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    charts: Mapped[list[dict[str, Any]]] = mapped_column(JSONCol, default=list)
    status: Mapped[str] = mapped_column(String(16), default="confirmed")
    confirmed_at: Mapped[datetime | None] = mapped_column(TimestampCol)
    created_at: Mapped[datetime] = mapped_column(TimestampCol)
    updated_at: Mapped[datetime] = mapped_column(TimestampCol)
