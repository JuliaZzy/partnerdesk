"""Tables for `schemas/partnership.py`.

`Distributor` has no schema module of its own; a partnership is the pair of a brand and one.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampCol
from .brand import Brand


class Distributor(Base):
    __tablename__ = "distributors"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    country: Mapped[str | None] = mapped_column(String(2))
    created_at: Mapped[datetime] = mapped_column(TimestampCol)
    updated_at: Mapped[datetime] = mapped_column(TimestampCol)


class Partnership(Base):
    __tablename__ = "partnerships"
    __table_args__ = (UniqueConstraint("brand_id", "distributor_id", name="uq_partnership_pair"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    distributor_id: Mapped[str] = mapped_column(ForeignKey("distributors.id"), index=True)
    ship_to_country: Mapped[str | None] = mapped_column(String(2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    created_at: Mapped[datetime] = mapped_column(TimestampCol)
    updated_at: Mapped[datetime] = mapped_column(TimestampCol)

    brand: Mapped[Brand] = relationship(lazy="joined")
    distributor: Mapped[Distributor] = relationship(lazy="joined")
