"""
NormalizedDocument — OCR / native text → chunking / retrieval / evidence.

Schema version 1.1 adds: page-level `text`, document `full_text`, standard enums for
`block_type` and `bbox_unit`, `derived` pipeline slots, and structured `metadata`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now_iso() -> str:
    """ISO 8601 UTC timestamp for `DocumentMetadata.processed_at`."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class BboxUnit(str, Enum):
    """
    Coordinate system for `BoundingBox` on a page.

    Prefer PAGE_RATIO for cross-provider stability (values in [0, 1], origin top-left).
    """

    PAGE_RATIO = "page_ratio"
    PAGE_PX = "page_px"
    PDF_POINTS = "pdf_points"


class BlockType(str, Enum):
    """Normalized block semantics — map vendor labels here, do not pass through raw provider names."""

    LINE = "line"
    PARAGRAPH = "paragraph"
    TABLE_CELL = "table_cell"
    HEADER = "header"
    FOOTER = "footer"
    KEY_VALUE = "key_value"
    UNKNOWN = "unknown"


class BoundingBox(BaseModel):
    """Axis-aligned rectangle on one page. Interpretation is fixed by `bbox_unit` on the parent `TextBlock`."""

    x0: float = Field(..., description="Left")
    y0: float = Field(..., description="Top")
    x1: float = Field(..., description="Right")
    y1: float = Field(..., description="Bottom")


class TextBlock(BaseModel):
    """Ordered unit on a page. Prefer `bbox_unit=page_ratio` with values in [0,1] for portability."""

    text: str
    bbox: Optional[BoundingBox] = None
    bbox_unit: Optional[BboxUnit] = Field(
        None,
        description="Required when bbox is set; page_ratio = normalized 0–1, top-left origin.",
    )
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    block_type: BlockType = BlockType.UNKNOWN

    @model_validator(mode="after")
    def _default_bbox_unit(self) -> TextBlock:
        if self.bbox is not None and self.bbox_unit is None:
            self.bbox_unit = BboxUnit.PAGE_RATIO
        return self

    @model_validator(mode="after")
    def _validate_ratio_bbox(self) -> TextBlock:
        if self.bbox is None or self.bbox_unit != BboxUnit.PAGE_RATIO:
            return self
        b = self.bbox
        for name, v in (("x0", b.x0), ("y0", b.y0), ("x1", b.x1), ("y1", b.y1)):
            if not 0.0 <= v <= 1.0:
                raise ValueError(f"bbox.{name} must be in [0,1] when bbox_unit is page_ratio, got {v}")
        return self


class NormalizedPage(BaseModel):
    """
    One page after OCR or text extraction.

    `text` is the canonical page-level plain string for downstream steps that do not need geometry.
    Prefer filling `text` from the provider; if empty, builders may join `blocks`.
    """

    page_number: int = Field(..., ge=1, description="1-based page index")
    width_px: Optional[float] = None
    height_px: Optional[float] = None
    text: str = Field(
        "",
        description="Stable page plain text — do not rely on re-joining blocks in consumers.",
    )
    blocks: list[TextBlock] = Field(
        default_factory=list,
        description="Finer-grained layout; granularity varies by provider.",
    )

    def page_text(self) -> str:
        """Prefer stored `text`; fallback to joining blocks."""
        if self.text and self.text.strip():
            return self.text
        parts = [b.text.strip() for b in self.blocks if b.text and b.text.strip()]
        return "\n".join(parts) if parts else ""


class NormalizedSection(BaseModel):
    """Section / heading / clause-group (from heuristics or future ML)."""

    model_config = ConfigDict(extra="allow")

    section_index: int = Field(0, ge=0, description="0-based order in document")
    title: str = Field("", description="Heading line or inferred title")
    level: int = Field(1, ge=1, description="1 = top-level (Article), deeper for 1.1 / (a)")
    page_start: int = Field(1, ge=1)
    page_end: int = Field(1, ge=1)
    char_start: Optional[int] = Field(None, description="Start offset in linearized full text used for chunking")
    char_end: Optional[int] = Field(None, description="End offset (exclusive) in linearized full text")


class NormalizedChunk(BaseModel):
    """One chunk for retrieval / LLM (downstream of NormalizedDocument)."""

    model_config = ConfigDict(extra="allow")

    chunk_index: int = 0
    text: str = ""
    page_start: int = Field(1, ge=1)
    page_end: int = Field(1, ge=1)
    section_hint: Optional[str] = None
    char_start: Optional[int] = Field(
        None,
        description="Start offset in the same linearized string used for chunking (see chunking._linearize_pages)",
    )
    char_end: Optional[int] = Field(
        None,
        description="Exclusive end offset in linearized text",
    )


class DerivedArtifacts(BaseModel):
    """Pipeline-derived structures; may stay empty at OCR exit."""

    sections: list[NormalizedSection] = Field(default_factory=list)
    chunks: list[NormalizedChunk] = Field(default_factory=list)


class DocumentMetadata(BaseModel):
    """Mix of required observability and provider-specific keys."""

    model_config = ConfigDict(extra="allow")

    page_count: int = Field(0, description="Should match len(pages); validated in NormalizedDocument.")
    processed_at: Optional[str] = Field(
        None,
        description="ISO 8601 UTC when normalization finished, e.g. 2026-03-31T12:00:00Z",
    )


class NormalizedDocument(BaseModel):
    """
    Canonical document for chunking + evidence.

    - `full_text`: document-level string for keyword scan, language detection, length, coarse type.
    - `pages[].text`: page-level stable text (in addition to blocks).
    - `derived`: placeholders for sections/chunks produced after OCR.
    """

    schema_version: Literal["1.1"] = "1.1"

    source_kind: Literal["ocr", "native_text", "mixed"] = "native_text"
    ocr_provider_id: str = Field(
        ...,
        description='Logical id, e.g. "textract", "glm", "native_pymupdf", "stub"',
    )
    ocr_provider_version: Optional[str] = None
    source_filename: Optional[str] = None
    mime_type: Optional[str] = None
    language_hint: Optional[str] = None

    full_text: str = Field(
        "",
        description="Full document plain text — use for quick filters before chunking.",
    )

    pages: list[NormalizedPage] = Field(default_factory=list)

    derived: DerivedArtifacts = Field(default_factory=DerivedArtifacts)

    metadata: DocumentMetadata = Field(default_factory=DocumentMetadata)

    @model_validator(mode="after")
    def _sync_text_and_counts(self) -> NormalizedDocument:
        # Per-page text from blocks if provider did not set page.text
        for p in self.pages:
            if not (p.text or "").strip() and p.blocks:
                p.text = "\n".join(b.text for b in p.blocks if b.text)

        # Document full_text from pages if not set
        if not (self.full_text or "").strip() and self.pages:
            self.full_text = "\n\n".join(p.page_text() for p in self.pages if p.page_text())

        self.metadata.page_count = len(self.pages)

        return self

    def flattened_text(self, page_separator: str = "\n\n") -> str:
        """Alias for reading convenience — returns `full_text` after sync."""
        return self.full_text or page_separator.join(p.page_text() for p in self.pages if p.page_text())
