"""Tables for `schemas/brand.py`."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampCol


class Brand(Base):
    __tablename__ = "brands"
    __table_args__ = (Index("ix_brands_name_trgm", "name", postgresql_using="gin", postgresql_ops={"name": "gin_trgm_ops"}),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    preferred_currency: Mapped[str] = mapped_column(String(3), default="USD")
    created_at: Mapped[datetime] = mapped_column(TimestampCol)
    updated_at: Mapped[datetime] = mapped_column(TimestampCol)


class BrandAgentSettings(Base):
    __tablename__ = "brand_agent_settings"

    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), primary_key=True)
    payment_terms: Mapped[str | None] = mapped_column(String(16))
    incoterms: Mapped[str | None] = mapped_column(String(8))
    shipping_method: Mapped[str | None] = mapped_column(String(16))
    eta_days: Mapped[int | None] = mapped_column(Integer)
    operating_context: Mapped[str | None] = mapped_column(Text)
    min_po_confidence: Mapped[float | None] = mapped_column(Float)
    tone: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(TimestampCol)
