"""OcrProvider protocol — implement per vendor (Textract, GLM, …)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..schemas import NormalizedDocument


@runtime_checkable
class OcrProvider(Protocol):
    """Maps a local file to NormalizedDocument."""

    provider_id: str

    def ocr_document(self, file_path: str, mime_type: str | None = None) -> NormalizedDocument:
        """
        Read file at `file_path` and return normalized OCR/text layout.

        Implementations may call cloud APIs; callers must handle timeouts and errors.
        """
        ...
