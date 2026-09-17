"""智谱 GLM-OCR：官方 zai-sdk `layout_parsing`（支持 PDF/图片 URL 或 base64，非 OpenAI vision chat）。"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from zai import ZhipuAiClient

from ._media import resolve_ocr_max_pages
from ..schemas import (
    BlockType,
    DerivedArtifacts,
    DocumentMetadata,
    NormalizedDocument,
    NormalizedPage,
    TextBlock,
    utc_now_iso,
)

DEFAULT_MODEL = "glm-ocr"


def _local_file_to_layout_file_param(path: str) -> str:
    """SDK: file 为 URL 或可解析的 PDF/图片 base64（含 data URL）。"""
    raw = Path(path).read_bytes()
    b64 = base64.standard_b64encode(raw).decode("ascii")
    lower = path.lower()
    if lower.endswith(".pdf"):
        return f"data:application/pdf;base64,{b64}"
    if lower.endswith((".jpg", ".jpeg")):
        return f"data:image/jpeg;base64,{b64}"
    if lower.endswith(".png"):
        return f"data:image/png;base64,{b64}"
    return f"data:application/octet-stream;base64,{b64}"


class GlmOcrProvider:
    provider_id = "glm"

    def ocr_document(self, file_path: str, mime_type: str | None = None) -> NormalizedDocument:
        api_key = os.environ.get("ZHIPU_API_KEY") or os.environ.get("GLM_API_KEY")
        if not api_key:
            raise RuntimeError("Set ZHIPU_API_KEY or GLM_API_KEY for GLM-OCR.")

        model = os.environ.get("GLM_OCR_MODEL", DEFAULT_MODEL)

        client = ZhipuAiClient(api_key=api_key)
        file_param = _local_file_to_layout_file_param(file_path)

        # PDF：按页区间限制调用量；默认 OCR 全页（见 resolve_ocr_max_pages）；单图不传页码
        lower = file_path.lower()
        extra: dict = {"model": model, "file": file_param}
        if lower.endswith(".pdf"):
            max_pages = resolve_ocr_max_pages(file_path)
            extra["start_page_id"] = 1
            extra["end_page_id"] = max(1, max_pages)

        resp = client.layout_parsing.create(**extra)

        pages = _pages_from_layout_response(resp)
        doc_mime = mime_type or (
            "application/pdf" if lower.endswith(".pdf") else "image/jpeg" if lower.endswith((".jpg", ".jpeg")) else "image/png"
        )

        return NormalizedDocument(
            source_kind="ocr",
            ocr_provider_id=self.provider_id,
            ocr_provider_version=model,
            source_filename=file_path.split("/")[-1].split("\\")[-1],
            mime_type=doc_mime,
            full_text="",
            pages=pages,
            derived=DerivedArtifacts(),
            metadata=DocumentMetadata.model_validate(
                {
                    "glm_sdk": "zai-sdk",
                    "layout_parsing_id": getattr(resp, "id", None),
                    "processed_at": utc_now_iso(),
                }
            ),
        )


def _pages_from_layout_response(resp: object) -> list[NormalizedPage]:
    """从 LayoutParsingResp 构造 NormalizedPage。"""
    md = getattr(resp, "md_results", None) or ""
    layout_details = getattr(resp, "layout_details", None)
    pages: list[NormalizedPage] = []

    if layout_details:
        for i, page_blocks in enumerate(layout_details):
            if not page_blocks:
                continue
            parts: list[str] = []
            for block in page_blocks:
                content = getattr(block, "content", None) or ""
                if content.strip():
                    parts.append(content.strip())
            text = "\n".join(parts)
            pages.append(
                NormalizedPage(
                    page_number=i + 1,
                    text=text,
                    blocks=[TextBlock(text=text, block_type=BlockType.PARAGRAPH)] if text else [],
                )
            )

    if not pages and md.strip():
        pages.append(
            NormalizedPage(
                page_number=1,
                text=md.strip(),
                blocks=[TextBlock(text=md.strip(), block_type=BlockType.PARAGRAPH)],
            )
        )

    if not pages:
        pages.append(NormalizedPage(page_number=1, text="", blocks=[]))

    return pages
