"""Tables for `schemas/catalog.py`."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Enum, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ...schemas.catalog import PRODUCT_STATUS_VALUES
from .base import Base, JSONCol, TimestampCol


def _trigram(name: str, column: str) -> Index:
    """A GIN trigram index: `ILIKE '%text%'` on a name stays an index scan at a million rows."""
    return Index(name, column, postgresql_using="gin", postgresql_ops={column: "gin_trgm_ops"})


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("brand_id", "sku", name="uq_product_sku_per_brand"),
        _trigram("ix_products_name_en_trgm", "product_name_en"),
        _trigram("ix_products_name_native_trgm", "product_name_native"),
        _trigram("ix_products_sku_trgm", "sku"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    sku: Mapped[str] = mapped_column(String(64))
    product_name_en: Mapped[str | None] = mapped_column(String(200))
    product_name_native: Mapped[str | None] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(100), index=True)
    subcategory: Mapped[str | None] = mapped_column(String(100))
    variant: Mapped[str | None] = mapped_column(String(100))
    form: Mapped[str | None] = mapped_column(String(100))
    unit_size: Mapped[str | None] = mapped_column(String(50))
    net_content: Mapped[str | None] = mapped_column(String(50))
    description_en: Mapped[str | None] = mapped_column(Text)
    case_pack: Mapped[int | None] = mapped_column(Integer)
    case_price: Mapped[float | None] = mapped_column(Float)
    moq_units: Mapped[int | None] = mapped_column(Integer)
    cases_per_layer: Mapped[int | None] = mapped_column(Integer)
    layers_per_pallet: Mapped[int | None] = mapped_column(Integer)
    cases_per_pallet: Mapped[int | None] = mapped_column(Integer)
    case_weight: Mapped[float | None] = mapped_column(Float)
    product_status: Mapped[str] = mapped_column(
        Enum(*PRODUCT_STATUS_VALUES, name="product_status"), default="正品", server_default="正品", index=True,
    )
    policy_tags: Mapped[list[str]] = mapped_column(JSONCol, default=list)
    restricted_territories: Mapped[list[str]] = mapped_column(JSONCol, default=list)
    created_at: Mapped[datetime] = mapped_column(TimestampCol)
    updated_at: Mapped[datetime] = mapped_column(TimestampCol)


class CommercialRule(Base):
    __tablename__ = "commercial_rules"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    # Set when the rule was derived from a contract — provenance an approver can follow.
    contract_id: Mapped[str | None] = mapped_column(ForeignKey("contracts.id", ondelete="SET NULL"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    rule_type: Mapped[str] = mapped_column(String(64))
    rule_config: Mapped[dict[str, Any]] = mapped_column(JSONCol, default=dict)
    severity: Mapped[str] = mapped_column(String(32), default="block")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    partner_scope: Mapped[str] = mapped_column(String(16), default="all")
    partner_ids: Mapped[list[str]] = mapped_column(JSONCol, default=list)
    partner_regions: Mapped[list[str]] = mapped_column(JSONCol, default=list)
    product_scope: Mapped[str] = mapped_column(String(16), default="all")
    product_ids: Mapped[list[str]] = mapped_column(JSONCol, default=list)
    product_roles: Mapped[list[str]] = mapped_column(JSONCol, default=list)
    product_tags: Mapped[list[str]] = mapped_column(JSONCol, default=list)
    effective_from: Mapped[str | None] = mapped_column(String(10))
    effective_until: Mapped[str | None] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(TimestampCol)
    updated_at: Mapped[datetime] = mapped_column(TimestampCol)
