"""Select OCR implementation via CONTRACT_OCR_PROVIDER (default: stub)."""

from __future__ import annotations

import os

from .base import OcrProvider
from .stub import StubOcrProvider


def get_ocr_provider_by_name(name: str) -> OcrProvider:
    """Return provider by explicit id (for CLI / tests)."""
    key = name.strip().lower()
    if key == "stub":
        return StubOcrProvider()
    if key == "textract":
        from .textract import TextractOcrProvider

        return TextractOcrProvider()
    if key == "glm":
        from .glm import GlmOcrProvider

        return GlmOcrProvider()
    if key == "openai":
        from .openai_vision import OpenAiVisionOcrProvider

        return OpenAiVisionOcrProvider()

    raise ValueError(
        f"Unknown OCR provider {name!r}. Use: stub, textract, glm, openai."
    )


def get_ocr_provider() -> OcrProvider:
    """
    Environment: CONTRACT_OCR_PROVIDER = stub | textract | glm | openai

    Defaults to stub when unset.
    """
    name = (os.environ.get("CONTRACT_OCR_PROVIDER") or "stub").strip().lower()
    return get_ocr_provider_by_name(name)
