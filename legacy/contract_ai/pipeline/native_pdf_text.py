"""
Build NormalizedDocument from a PDF **text layer only** (no OCR).

This approximates the Node path `extractContractText` → pdf-parse: selectable text only.
Scanned PDFs yield nearly empty text — same as Node returning `needs_better_file`.
"""

from __future__ import annotations

from pathlib import Path

from ..schemas import (
    BlockType,
    DerivedArtifacts,
    DocumentMetadata,
    NormalizedDocument,
    NormalizedPage,
    TextBlock,
    utc_now_iso,
)


def pdf_text_layer_to_normalized(pdf_path: str) -> NormalizedDocument:
    import fitz  # PyMuPDF — same family as rendering; get_text reads embedded fonts / text objects.

    path = Path(pdf_path)
    doc = fitz.open(str(path))
    pages: list[NormalizedPage] = []
    try:
        for i in range(len(doc)):
            page = doc.load_page(i)
            text = (page.get_text("text") or "").strip()
            block = (
                TextBlock(text=text, block_type=BlockType.PARAGRAPH)
                if text
                else TextBlock(text="", block_type=BlockType.PARAGRAPH)
            )
            pages.append(
                NormalizedPage(
                    page_number=i + 1,
                    text=text,
                    blocks=[block] if text else [],
                )
            )
    finally:
        doc.close()

    parts = [p.page_text() for p in pages if p.page_text()]
    full = "\n\n".join(parts)

    return NormalizedDocument(
        source_kind="native_text",
        ocr_provider_id="native_pdf_text_layer",
        ocr_provider_version="pymupdf-get_text",
        source_filename=path.name,
        mime_type="application/pdf",
        full_text=full,
        pages=pages,
        derived=DerivedArtifacts(),
        metadata=DocumentMetadata.model_validate(
            {
                "native_pdf_text_layer": True,
                "processed_at": utc_now_iso(),
            }
        ),
    )
