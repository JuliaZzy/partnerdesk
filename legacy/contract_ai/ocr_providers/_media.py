"""Load PDF pages or raster images as PNG bytes for OCR APIs."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _read_file(path: str) -> bytes:
    return Path(path).read_bytes()


def is_pdf(path: str) -> bool:
    p = path.lower()
    return p.endswith(".pdf")


def is_raster_image(path: str) -> bool:
    return path.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"))


def pdf_page_count(pdf_path: str) -> int:
    """Total pages in a PDF (for default 'OCR all pages' behavior)."""
    import fitz  # PyMuPDF

    doc = fitz.open(pdf_path)
    try:
        return len(doc)
    finally:
        doc.close()


def resolve_ocr_max_pages(pdf_path: str) -> int:
    """
    Pages to OCR / render from a PDF.

    Env CONTRACT_OCR_MAX_PAGES:
    - unset, empty, 0, all, max, -1 → use full document page count
    - positive integer → cap at that many pages (and at actual PDF length)
    """
    raw = (os.environ.get("CONTRACT_OCR_MAX_PAGES") or "").strip().lower()
    total = max(1, pdf_page_count(pdf_path))
    if raw in ("", "0", "all", "max", "-1"):
        return total
    try:
        cap = int(raw)
    except ValueError:
        return total
    if cap <= 0:
        return total
    return min(cap, total)


def render_pdf_pages_to_png_bytes(
    pdf_path: str,
    *,
    max_pages: int | None = None,
    dpi: int = 150,
    verbose: bool = False,
) -> list[bytes]:
    """Render each PDF page to PNG bytes (for Textract / vision OCR)."""
    import fitz  # PyMuPDF

    cap = max_pages
    if cap is None:
        cap = resolve_ocr_max_pages(pdf_path)
    out: list[bytes] = []
    doc = fitz.open(pdf_path)
    try:
        n = min(len(doc), max(1, cap))
        if verbose:
            print(
                f"[pdf render] {n} page(s) at {dpi} DPI (local PyMuPDF, no API yet)…",
                file=sys.stderr,
                flush=True,
            )
        for i in range(n):
            page = doc.load_page(i)
            pix = page.get_pixmap(dpi=dpi)
            out.append(pix.tobytes("png"))
            if verbose:
                print(f"[pdf render] page {i + 1}/{n} rasterized", file=sys.stderr, flush=True)
    finally:
        doc.close()
    return out


def load_pages_as_image_bytes(path: str) -> tuple[list[tuple[bytes, str]], str]:
    """
    Returns (list of (image bytes, image/* mime for data URL), document mime hint).

    PDF pages are rendered as PNG. Raster files pass through as one page.
    """
    if is_pdf(path):
        pngs = render_pdf_pages_to_png_bytes(path)
        pages = [(b, "image/png") for b in pngs]
        return pages, "application/pdf"
    if is_raster_image(path):
        raw = _read_file(path)
        mime = "image/jpeg" if path.lower().endswith((".jpg", ".jpeg")) else "image/png"
        return [(raw, mime)], mime
    raise ValueError(
        f"Unsupported file for OCR test: {path}. Use PDF, PNG, or JPEG."
    )


def data_url_image(image_bytes: bytes, mime: str = "image/png") -> str:
    import base64

    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


# Backwards alias
def load_pages_as_png_bytes(path: str) -> tuple[list[bytes], str]:
    """Deprecated name — returns only bytes list + doc mime (PDF → all PNG pages)."""
    pages, doc_mime = load_pages_as_image_bytes(path)
    return [p[0] for p in pages], doc_mime
