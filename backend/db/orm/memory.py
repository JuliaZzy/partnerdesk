"""Table for `schemas/memory.py`.

What one person at a distributor wants the agent to keep in mind — a standing
preference, an instruction, a fact learned in chat. Colleagues do not share these.
Read into each agent's prompt; context only, it can never relax a rule or change a price.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampCol


class Memory(Base):
    __tablename__ = "memories"
    __table_args__ = (Index("ix_memories_partnership_active", "partnership_id", "is_active"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)  # insertion order = recall order
    partnership_id: Mapped[str] = mapped_column(ForeignKey("partnerships.id"), index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # preference | fact | instruction | event
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="user")  # user | chat | system
    session_id: Mapped[str | None] = mapped_column(ForeignKey("sessions.id", ondelete="SET NULL"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_recalled_at: Mapped[datetime | None] = mapped_column(TimestampCol)
    created_at: Mapped[datetime] = mapped_column(TimestampCol)
    updated_at: Mapped[datetime] = mapped_column(TimestampCol)
