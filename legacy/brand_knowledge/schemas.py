"""Pydantic models for the brand knowledge distillation pipeline.

A distilled document is a flat list of typed *fragments* rather than one fixed
schema: uploaded material is never only product specs — it also carries brand
story, sales proof, target-audience profiles, certifications, and whatever else
the brand happens to put in a deck. Forcing everything into a product shape
silently drops the rest, so the type is an open string (see SEED_TYPES) and the
model may invent a new one when nothing fits.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# Starting taxonomy handed to the model. Not exhaustive and not enforced — the
# model is told to invent a new slug when none of these fit, and anything
# unrecognized surfaces in the UI for the brand to relabel or re-type.
SEED_TYPES: dict[str, str] = {
    "product": "A single product: what it is, selling points, differentiation, specs.",
    "brand_story": "Brand origin, founder story, mission, values, heritage, culture.",
    "proof": "Evidence of traction: sales figures, growth, market share, awards, press.",
    "audience": "Target customer/market profiles — who this is for and why.",
    "certification": "Certifications, test reports, regulatory or compliance status.",
    "case_study": "A named customer, distributor, or market success story.",
    "market": "Category or market context: size, trends, competitive landscape.",
    "other": "Genuinely relevant material that fits none of the above.",
}


class DiscoveredType(BaseModel):
    """One kind of content the model found in the material (pass 1)."""

    type: str = Field(description="Slug, snake_case. A SEED_TYPES key when one fits, else a new one.")
    label: str = Field(description="Short human-readable name for this kind of content.")
    description: str = Field(description="One sentence on what this material covers.")
    page_numbers: list[int] = Field(default_factory=list, description="1-based pages where it appears.")


class DocumentScan(BaseModel):
    """Pass 1 output: what is actually in this document, before any extraction."""

    summary: str = Field(description="Two or three sentences describing the document as a whole.")
    types: list[DiscoveredType] = Field(default_factory=list)


class KnowledgeFragment(BaseModel):
    """One self-contained, reusable piece of brand knowledge (pass 2)."""

    type: str = Field(description="Slug from the pass-1 scan.")
    title: str = Field(description="Short human-readable title, e.g. the product name.")
    content: str = Field(description="The distilled knowledge, in prose. Complete on its own.")
    product_name: str | None = Field(
        default=None,
        description="Product this is about, verbatim as written in the source. Only when type is 'product'.",
    )
    source_pages: list[int] = Field(default_factory=list, description="1-based pages this came from.")
    tags: list[str] = Field(default_factory=list, description="A few short keywords for filtering.")


class FragmentBatch(BaseModel):
    """Pass 2 model response."""

    fragments: list[KnowledgeFragment] = Field(default_factory=list)


class DistillResult(BaseModel):
    """Everything one uploaded file yielded."""

    filename: str
    page_count: int
    pages_processed: int
    summary: str
    types: list[DiscoveredType] = Field(default_factory=list)
    fragments: list[KnowledgeFragment] = Field(default_factory=list)
    model: str
