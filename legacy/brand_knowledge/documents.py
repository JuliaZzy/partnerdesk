"""Load an uploaded PDF as pages the model can actually see.

Brand decks put most of their meaning in charts, layout, and imagery — a text-only
extract of a slide that reads "Clinically proven" drops the chart next to it that
says by how much. So every page carries both a rendered image and its text layer,
and both go to the multimodal model.

PDF only, by design: brands can export a deck to PDF themselves, and every other
format would need either a fragile text-only reader or a LibreOffice-class
dependency in the deploy image.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DPI = 150
DEFAULT_MAX_PAGES = 200
# Pages go to the model as JPEG, not PNG. A 150-DPI slide carrying a photo is
# several times larger as PNG, and it is the size of the single request — not the
# page count — that a corporate proxy or VPN drops once it grows past ~1 MB. Width
# is capped so the request stays small at no cost to image tokens: Gemini tiles an
# image by its dimensions, and a 1600px-wide slide is already at that ceiling, so
# downscaling to it removes bytes without removing anything the model reads.
DEFAULT_JPEG_QUALITY = 72
DEFAULT_MAX_WIDTH_PX = 1600

SUPPORTED_EXTS = (".pdf",)


class UnsupportedDocument(ValueError):
    """Raised for a file type this pipeline can't read."""


@dataclass
class SourcePage:
    """One page: its text layer plus its rendered image."""

    page_number: int
    text: str = ""
    images: list[tuple[bytes, str]] = field(default_factory=list)


@dataclass
class LoadedDocument:
    filename: str
    page_count: int
    pages: list[SourcePage]

    @property
    def pages_processed(self) -> int:
        return len(self.pages)


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _env_int(name: str, default: int, *, lo: int, hi: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    try:
        val = int(raw)
    except ValueError:
        return default
    return max(lo, min(val, hi))


def _render_dpi() -> int:
    return _env_int("BRAND_KNOWLEDGE_DPI", DEFAULT_DPI, lo=72, hi=300)


def _jpeg_quality() -> int:
    return _env_int("BRAND_KNOWLEDGE_JPEG_QUALITY", DEFAULT_JPEG_QUALITY, lo=30, hi=95)


def _max_width_px() -> int:
    return _env_int("BRAND_KNOWLEDGE_MAX_WIDTH_PX", DEFAULT_MAX_WIDTH_PX, lo=800, hi=4000)


def _render_jpeg_pages(
    path: str, cap: int, dpi: int, quality: int, max_width: int
) -> list[bytes]:
    """Render the first `cap` pages to JPEG, downscaling any page wider than `max_width`.

    Kept local rather than reusing the contract OCR PNG renderer: that path feeds
    Textract, which wants lossless PNG, whereas the model here wants the smallest
    request that still reads cleanly. Encoding straight from the pixmap avoids a
    PNG round-trip and needs no extra image library.
    """
    import fitz  # PyMuPDF

    out: list[bytes] = []
    doc = fitz.open(path)
    try:
        n = min(len(doc), max(1, cap))
        for i in range(n):
            page = doc.load_page(i)
            points_wide = page.rect.width or max_width
            # Never upscale: effective DPI only drops, and only when a page at the
            # target DPI would exceed max_width.
            eff_dpi = min(dpi, int(max_width * 72.0 / points_wide))
            pix = page.get_pixmap(dpi=max(36, eff_dpi))
            out.append(pix.tobytes("jpeg", jpg_quality=quality))
    finally:
        doc.close()
    return out


def _max_pages() -> int:
    return _env_int("BRAND_KNOWLEDGE_MAX_PAGES", DEFAULT_MAX_PAGES, lo=1, hi=1000)


def _page_text(path: str, count: int) -> list[str]:
    """Text layer per page. Scanned PDFs return empty strings — the image carries them."""
    import fitz  # PyMuPDF

    doc = fitz.open(path)
    try:
        return [doc.load_page(i).get_text().strip() for i in range(min(count, len(doc)))]
    finally:
        doc.close()


def load_document(path: str) -> LoadedDocument:
    """Read an uploaded PDF into pages. Raises UnsupportedDocument for anything else."""
    if not path.lower().endswith(SUPPORTED_EXTS):
        raise UnsupportedDocument(
            f"Unsupported file type: {Path(path).name}. Upload a PDF — "
            "export slides or documents to PDF first."
        )

    import fitz  # PyMuPDF

    doc = fitz.open(path)
    try:
        total = len(doc)
    finally:
        doc.close()

    cap = min(total, _max_pages())
    if cap < total:
        _log(
            f"[brand-knowledge] {Path(path).name!r}: {total} pages, "
            f"processing first {cap} (BRAND_KNOWLEDGE_MAX_PAGES)."
        )

    images = _render_jpeg_pages(path, cap, _render_dpi(), _jpeg_quality(), _max_width_px())
    texts = _page_text(path, cap)

    pages = [
        SourcePage(
            page_number=i + 1,
            text=texts[i] if i < len(texts) else "",
            images=[(jpg, "image/jpeg")],
        )
        for i, jpg in enumerate(images)
    ]
    return LoadedDocument(filename=Path(path).name, page_count=total, pages=pages)
