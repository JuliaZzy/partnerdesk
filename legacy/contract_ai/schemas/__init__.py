"""Contract AI schemas — shared between OCR, chunking, and extraction."""

from .normalized_document import (
    BlockType,
    BoundingBox,
    BboxUnit,
    DerivedArtifacts,
    DocumentMetadata,
    NormalizedChunk,
    NormalizedDocument,
    NormalizedPage,
    NormalizedSection,
    TextBlock,
    utc_now_iso,
)

__all__ = [
    "BlockType",
    "BoundingBox",
    "BboxUnit",
    "DerivedArtifacts",
    "DocumentMetadata",
    "NormalizedChunk",
    "NormalizedDocument",
    "NormalizedPage",
    "NormalizedSection",
    "TextBlock",
    "utc_now_iso",
]
