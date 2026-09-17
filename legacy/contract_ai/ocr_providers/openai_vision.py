"""OpenAI vision chat (default: gpt-4o-mini) — per-page image transcription → NormalizedDocument."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx
from openai import APIConnectionError, OpenAI

from ._media import (
    data_url_image,
    is_pdf,
    load_pages_as_image_bytes,
    render_pdf_pages_to_png_bytes,
    resolve_ocr_max_pages,
)
from ..schemas import (
    BlockType,
    DerivedArtifacts,
    DocumentMetadata,
    NormalizedDocument,
    NormalizedPage,
    TextBlock,
    utc_now_iso,
)

DEFAULT_OCR_MODEL = "gpt-4o-mini"

OCR_USER_PROMPT = """Transcribe all visible text from this document page. Preserve reading order (top to bottom, left to right). Include headers, body text, table cells, footnotes, signatures, and dates. For tables, use rows separated by newlines. Do not summarize or omit content. If the page is blank or illegible, output exactly: [blank]
Output only the transcribed text — no preamble or commentary."""


def _resolve_ocr_dpi() -> int:
    raw = (os.environ.get("CONTRACT_OCR_DPI") or "150").strip()
    try:
        d = int(raw)
    except ValueError:
        return 150
    return max(72, min(d, 300))


def _resolve_image_detail() -> str:
    """OpenAI vision: `low` = smaller payload (better through proxies); `high` = sharper, heavier."""
    raw = (os.environ.get("CONTRACT_OCR_IMAGE_DETAIL") or "low").strip().lower()
    if raw in ("high", "low", "auto"):
        return raw
    return "low"


def _openai_timeout() -> httpx.Timeout:
    """Vision uploads can be large; proxies often cut default short timeouts."""
    try:
        sec = float((os.environ.get("CONTRACT_OCR_OPENAI_TIMEOUT_SEC") or "600").strip())
    except ValueError:
        sec = 600.0
    sec = max(60.0, min(sec, 3600.0))
    return httpx.Timeout(sec)


def _load_page_images(file_path: str) -> tuple[list[tuple[bytes, str]], str]:
    """Same as load_pages_as_image_bytes but honors CONTRACT_OCR_DPI for PDFs."""
    if is_pdf(file_path):
        cap = resolve_ocr_max_pages(file_path)
        dpi = _resolve_ocr_dpi()
        pngs = render_pdf_pages_to_png_bytes(
            file_path,
            max_pages=cap,
            dpi=dpi,
            verbose=True,
        )
        return [(b, "image/png") for b in pngs], "application/pdf"
    return load_pages_as_image_bytes(file_path)


def _log_ocr(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _openai_client() -> OpenAI:
    on_replit = "REPL_ID" in os.environ
    if on_replit:
        key = os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY")
        if not key:
            raise RuntimeError(
                "Set AI_INTEGRATIONS_OPENAI_API_KEY for OpenAI vision OCR on Replit."
            )
        base = os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")
    else:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(
                "Set OPENAI_API_KEY in the repo root .env for OpenAI vision OCR."
            )
        base = os.environ.get("OPENAI_BASE_URL")
    kw: dict = {"api_key": key, "timeout": _openai_timeout()}
    if base and base.strip():
        kw["base_url"] = base.strip()
    try:
        retries = int((os.environ.get("CONTRACT_OCR_OPENAI_MAX_RETRIES") or "3").strip())
    except ValueError:
        retries = 3
    kw["max_retries"] = max(0, min(retries, 10))
    return OpenAI(**kw)


class OpenAiVisionOcrProvider:
    provider_id = "openai"

    def ocr_document(self, file_path: str, mime_type: str | None = None) -> NormalizedDocument:
        model = (os.environ.get("CONTRACT_OCR_MODEL") or DEFAULT_OCR_MODEL).strip()
        client = _openai_client()
        t_wall = time.perf_counter()

        _log_ocr(
            f"[openai vision] Preparing images from {Path(file_path).name!r} "
            f"(one API call per page — long PDFs take several minutes).",
        )
        t0 = time.perf_counter()
        page_images, detected_mime = _load_page_images(file_path)
        doc_mime = mime_type or detected_mime
        n_pages = len(page_images)
        detail = _resolve_image_detail()
        _log_ocr(
            f"[openai vision] {n_pages} page image(s) ready in {time.perf_counter() - t0:.1f}s. "
            f"Calling model={model!r} detail={detail!r} …",
        )

        pages: list[NormalizedPage] = []
        for idx, (img_bytes, img_mime) in enumerate(page_images):
            url = data_url_image(img_bytes, img_mime)
            page_num = idx + 1
            approx_kb = len(img_bytes) / 1024.0
            _log_ocr(
                f"[openai vision] API page {page_num}/{n_pages} (~{approx_kb:.0f} KB image) …",
            )
            t_page = time.perf_counter()
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": OCR_USER_PROMPT},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": url, "detail": detail},
                                },
                            ],
                        }
                    ],
                    max_tokens=16384,
                    temperature=0.0,
                )
            except APIConnectionError as e:
                _log_ocr(
                    "[openai vision] Connection failed (proxy/firewall often drops large vision requests). "
                    "Try: unset HTTPS_PROXY if misconfigured; set CONTRACT_OCR_IMAGE_DETAIL=low; "
                    "lower CONTRACT_OCR_DPI (e.g. 120); or use CONTRACT_OCR_PROVIDER=glm.",
                )
                raise RuntimeError(
                    f"OpenAI vision request failed: {e}. See stderr for hints."
                ) from e
            text = (resp.choices[0].message.content or "").strip()
            dt = time.perf_counter() - t_page
            _log_ocr(
                f"[openai vision] API page {page_num}/{n_pages} done in {dt:.1f}s "
                f"({len(text)} chars).",
            )
            if text == "[blank]":
                text = ""
            block = (
                TextBlock(text=text, block_type=BlockType.PARAGRAPH)
                if text
                else TextBlock(text="", block_type=BlockType.PARAGRAPH)
            )
            pages.append(
                NormalizedPage(
                    page_number=idx + 1,
                    text=text,
                    blocks=[block] if text else [],
                )
            )

        _log_ocr(
            f"[openai vision] OCR complete: {n_pages} page(s) in "
            f"{time.perf_counter() - t_wall:.1f}s wall time.",
        )

        return NormalizedDocument(
            source_kind="ocr",
            ocr_provider_id=self.provider_id,
            ocr_provider_version=model,
            source_filename=Path(file_path).name,
            mime_type=doc_mime,
            full_text="",
            pages=pages,
            derived=DerivedArtifacts(),
            metadata=DocumentMetadata.model_validate(
                {
                    "openai_vision": True,
                    "processed_at": utc_now_iso(),
                }
            ),
        )
