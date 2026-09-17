"""AWS Textract DetectDocumentText → NormalizedDocument."""

from __future__ import annotations

import os
from typing import Any

from ..schemas import (
    BlockType,
    BoundingBox,
    BboxUnit,
    DerivedArtifacts,
    DocumentMetadata,
    NormalizedDocument,
    NormalizedPage,
    TextBlock,
    utc_now_iso,
)
from ._media import load_pages_as_image_bytes


class TextractOcrProvider:
    provider_id = "textract"

    def ocr_document(self, file_path: str, mime_type: str | None = None) -> NormalizedDocument:
        try:
            import boto3
        except ImportError as e:
            raise RuntimeError("Install boto3 for Textract: pip install boto3") from e

        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
        client = boto3.client("textract", region_name=region)

        page_images, detected_mime = load_pages_as_image_bytes(file_path)
        mime = mime_type or detected_mime

        pages: list[NormalizedPage] = []
        for idx, (img_bytes, _img_mime) in enumerate(page_images):
            resp = client.detect_document_text(Document={"Bytes": img_bytes})
            page = _textract_response_to_page(resp, page_number=idx + 1)
            pages.append(page)

        return NormalizedDocument(
            source_kind="ocr",
            ocr_provider_id=self.provider_id,
            source_filename=file_path.split("/")[-1].split("\\")[-1],
            mime_type=mime,
            full_text="",
            pages=pages,
            derived=DerivedArtifacts(),
            metadata=DocumentMetadata.model_validate(
                {
                    "aws_region": region,
                    "processed_at": utc_now_iso(),
                }
            ),
        )


def _textract_response_to_page(resp: dict[str, Any], page_number: int) -> NormalizedPage:
    blocks_out: list[TextBlock] = []
    lines: list[str] = []

    for block in resp.get("Blocks") or []:
        if block.get("BlockType") != "LINE":
            continue
        text = (block.get("Text") or "").strip()
        if not text:
            continue
        lines.append(text)
        geom = block.get("Geometry") or {}
        bb = geom.get("BoundingBox") or {}
        if bb and all(k in bb for k in ("Left", "Top", "Width", "Height")):
            left = float(bb["Left"])
            top = float(bb["Top"])
            w = float(bb["Width"])
            h = float(bb["Height"])
            bbox = BoundingBox(x0=left, y0=top, x1=left + w, y1=top + h)
            blocks_out.append(
                TextBlock(
                    text=text,
                    bbox=bbox,
                    bbox_unit=BboxUnit.PAGE_RATIO,
                    block_type=BlockType.LINE,
                )
            )
        else:
            blocks_out.append(TextBlock(text=text, block_type=BlockType.LINE))

    page_text = "\n".join(lines)
    return NormalizedPage(page_number=page_number, text=page_text, blocks=blocks_out)
