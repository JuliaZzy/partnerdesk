"""Read an uploaded file into pages the rest of the agent can use.

PDF and Word go through PyMuPDF / python-docx (the `contract` extra). Plain text is
split on form feeds, or into chunks when it has none, so a later citation still has a
page number. A PDF page also renders to a small JPEG: decks and scanned agreements put
the fact in a chart or a photo, and the text layer of that page is empty.
"""

from __future__ import annotations

import io
import re

PSEUDO_PAGE_CHARS = 3500
MAX_PDF_PAGES = 40
JPEG_QUALITY = 72
MAX_IMAGE_WIDTH = 1600

SUPPORTED_EXTS = (".pdf", ".txt", ".md", ".docx")


def _chunk(text: str, size: int = PSEUDO_PAGE_CHARS) -> list[str]:
    text = text.strip()
    if not text:
        return []
    out, buf = [], ""
    for para in re.split(r"\n\s*\n", text):
        if buf and len(buf) + len(para) + 2 > size:
            out.append(buf)
            buf = ""
        buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        out.append(buf)
    return out


def load_pages(file_name: str, data: bytes) -> list[tuple[int, str]]:
    """(page_number, text) per page. PDF via PyMuPDF; docx / txt / md are chunked into
    pseudo-pages so a fragment can still point somewhere."""
    name = file_name.lower()
    if name.endswith(".pdf"):
        try:
            import pymupdf
        except ImportError as err:
            raise ValueError('Reading PDFs needs PyMuPDF: pip install -e ".[contract]"') from err
        pages: list[tuple[int, str]] = []
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            for i, page in enumerate(doc, start=1):
                if i > MAX_PDF_PAGES:
                    break
                pages.append((i, (page.get_text() or "").strip()))
        return pages
    if name.endswith(".docx"):
        import docx

        d = docx.Document(io.BytesIO(data))
        text = "\n\n".join(p.text for p in d.paragraphs if p.text.strip())
        for t in d.tables:
            text += "\n\n" + "\n".join(" | ".join(c.text.strip() for c in row.cells) for row in t.rows)
        return list(enumerate(_chunk(text), start=1))
    if name.endswith((".txt", ".md")):
        text = data.decode("utf-8", errors="replace")
        parts = [p for p in text.split("\f") if p.strip()] if "\f" in text else _chunk(text)
        return list(enumerate(parts, start=1))
    raise ValueError(f"Unsupported document: {file_name} (use {', '.join(SUPPORTED_EXTS)})")


def render_pdf_images(data: bytes, page_numbers: list[int] | None = None) -> dict[int, bytes]:
    """JPEG per PDF page, downscaled so a chart still reads and the request stays small.

    `page_numbers` limits which 1-based pages are rendered. Empty when PyMuPDF is missing
    is a hard error — a caller that has a PDF already needed it to read the text layer.
    """
    try:
        import pymupdf
    except ImportError as err:
        raise ValueError('Reading PDFs needs PyMuPDF: pip install -e ".[contract]"') from err
    wanted = set(page_numbers) if page_numbers else None
    out: dict[int, bytes] = {}
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        for i, page in enumerate(doc, start=1):
            if i > MAX_PDF_PAGES:
                break
            if wanted is not None and i not in wanted:
                continue
            wide = page.rect.width or MAX_IMAGE_WIDTH
            dpi = min(150, int(MAX_IMAGE_WIDTH * 72.0 / wide))
            pix = page.get_pixmap(dpi=max(36, dpi))
            out[i] = pix.tobytes("jpeg", jpg_quality=JPEG_QUALITY)
    return out
