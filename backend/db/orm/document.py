"""Tables for `schemas/document.py`.

A brand's own material (decks, catalogues, brand books): one row per file, its text
per page, and the original bytes so a later index can render page images. Retrieval
reads the page text (`brand_knowledge.rag`), not these bytes. Deferred bytes: listing
documents must not pull the file.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampCol


class BrandDocument(Base):
    __tablename__ = "brand_documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), index=True)
    title: Mapped[str | None] = mapped_column(String(200))
    file_name: Mapped[str] = mapped_column(String(255))
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    file_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    media_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TimestampCol)
    updated_at: Mapped[datetime] = mapped_column(TimestampCol)


class BrandDocumentPage(Base):
    """The text layer of one page."""

    __tablename__ = "brand_document_pages"
    __table_args__ = (UniqueConstraint("document_id", "page_number", name="uq_document_page"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("brand_documents.id", ondelete="CASCADE"), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
