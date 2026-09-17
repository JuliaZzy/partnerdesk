"""Minimal provider for tests — replace with Textract / GLM implementations."""

from __future__ import annotations

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


class StubOcrProvider:
    provider_id = "stub"

    def ocr_document(self, file_path: str, mime_type: str | None = None) -> NormalizedDocument:
        line = "[stub ocr] replace StubOcrProvider with a real OcrProvider."
        return NormalizedDocument(
            source_kind="ocr",
            ocr_provider_id=self.provider_id,
            source_filename=file_path.split("/")[-1].split("\\")[-1],
            mime_type=mime_type,
            full_text=line,
            pages=[
                NormalizedPage(
                    page_number=1,
                    text=line,
                    blocks=[
                        TextBlock(
                            text=line,
                            block_type=BlockType.LINE,
                            bbox=BoundingBox(x0=0.05, y0=0.05, x1=0.95, y1=0.08),
                            bbox_unit=BboxUnit.PAGE_RATIO,
                        )
                    ],
                )
            ],
            derived=DerivedArtifacts(),
            metadata=DocumentMetadata.model_validate(
                {
                    "file_path": file_path,
                    "processed_at": utc_now_iso(),
                }
            ),
        )
