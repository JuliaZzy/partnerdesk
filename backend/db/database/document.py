"""Brand documents and the per-page text extracted from them."""

from __future__ import annotations

from sqlalchemy import select

from ...schemas import BrandDocument
from .. import orm
from .support import _row_dict


class DocumentMixin:
    def save_document(self, doc: BrandDocument, pages: list[tuple[int, str]], *, file_bytes: bytes | None = None) -> str:
        """The uploaded file as a row plus its text per page.
        `file_bytes` is the original upload, kept so a later index can render page images."""
        values = doc.model_dump(exclude={"created_at", "updated_at"})
        if file_bytes is not None:
            values["file_bytes"] = file_bytes
        with self.tx() as s:
            self._upsert(s, orm.BrandDocument, doc.id, values)
            s.flush()
            for row in s.scalars(select(orm.BrandDocumentPage).where(orm.BrandDocumentPage.document_id == doc.id)):
                s.delete(row)
            s.flush()
            s.add_all(orm.BrandDocumentPage(document_id=doc.id, page_number=n, text=t) for n, t in pages)
        return doc.id

    def documents(self, brand_id: str) -> list[BrandDocument]:
        with self.tx() as s:
            rows = s.scalars(select(orm.BrandDocument).where(orm.BrandDocument.brand_id == brand_id).order_by(orm.BrandDocument.created_at.desc())).all()
            return [BrandDocument(**_row_dict(r)) for r in rows]

    def document(self, id: str) -> BrandDocument | None:
        with self.tx() as s:
            row = s.get(orm.BrandDocument, id)
            return BrandDocument(**_row_dict(row)) if row else None

    def document_bytes(self, document_id: str) -> bytes | None:
        with self.tx() as s:
            row = s.get(orm.BrandDocument, document_id)
            return row.file_bytes if row else None

    def document_pages(self, document_id: str) -> list[tuple[int, str]]:
        with self.tx() as s:
            rows = s.scalars(select(orm.BrandDocumentPage).where(orm.BrandDocumentPage.document_id == document_id).order_by(orm.BrandDocumentPage.page_number)).all()
            return [(r.page_number, r.text) for r in rows]

    def delete_document(self, id: str) -> None:
        """Pages go with the document (ON DELETE CASCADE)."""
        with self.tx() as s:
            row = s.get(orm.BrandDocument, id)
            if row is not None:
                s.delete(row)
