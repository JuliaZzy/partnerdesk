"""Table for `schemas/user.py`."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampCol


class User(Base):
    """A person at a distributor. The desk switches among these; there is no password.

    Chats and memories belong to one person. Orders, contracts, and reports stay on the
    partnership, so colleagues at the same distributor share them.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    distributor_id: Mapped[str] = mapped_column(ForeignKey("distributors.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(TimestampCol)
    updated_at: Mapped[datetime] = mapped_column(TimestampCol)
