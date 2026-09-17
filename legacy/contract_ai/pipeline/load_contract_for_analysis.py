"""
Build NormalizedDocument for contract analysis: native DOCX vs OCR (PDF / images).
"""

from __future__ import annotations

import os
from pathlib import Path


def _suffix(path: str) -> str:
    return Path(path).suffix.lower()


def load_contract_normalized_document(file_path: str):
    """
    - `.docx` → native text (python-docx), no OCR.
    - `.pdf` and raster images → configured OCR provider (default: glm unless CONTRACT_OCR_PROVIDER is set).
    """
    from ..ocr_providers.native_docx import docx_to_normalized_document
    from ..ocr_providers.registry import get_ocr_provider_by_name

    p = file_path.strip()
    if not p:
        raise ValueError("Empty file path")

    suf = _suffix(p)

    if suf == ".docx":
        return docx_to_normalized_document(p)

    if suf == ".doc":
        raise RuntimeError(
            "Legacy .doc is not supported. Please save as .docx or PDF and re-upload."
        )

    if suf == ".pdf":
        # Try the embedded text layer first — text-based PDFs (e.g. from Word or Gotenberg)
        # have enough selectable text and don't need OCR.  Scanned PDFs yield nearly empty
        # text, so we fall through to the configured OCR provider.
        from .native_pdf_text import pdf_text_layer_to_normalized
        native_doc = pdf_text_layer_to_normalized(p)
        # 200 chars is a conservative threshold: a scanned page typically returns 0–10 chars.
        if len(native_doc.full_text.strip()) >= 200:
            return native_doc

    # PDF (scanned / image-only) + raster images → configured OCR provider
    ocr_name = (os.environ.get("CONTRACT_OCR_PROVIDER") or "glm").strip().lower()
    provider = get_ocr_provider_by_name(ocr_name)
    return provider.ocr_document(p)


def supported_analyze_suffixes() -> frozenset[str]:
    return frozenset(
        {
            ".pdf",
            ".docx",
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".tif",
            ".tiff",
        }
    )
