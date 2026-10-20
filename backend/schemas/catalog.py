"""Products and the commercial rules that gate an order."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# What a product is, commercially — one value, spelled the way the brand's staff and the
# model read it. Discontinued is a status rather than a flag: a discontinued item is no
# longer sold as anything, so it never needs to be a gift or a sample at the same time.
ProductStatus = Literal["正品", "赠品", "样品", "停产"]
PRODUCT_STATUS_VALUES: tuple[str, ...] = ("正品", "赠品", "样品", "停产")
# Rule configs and contract extractions name these in English.
PRODUCT_STATUS_ALIASES: dict[str, str] = {"regular": "正品", "gift": "赠品", "sample": "样品", "discontinued": "停产"}


def product_status(value: str) -> str:
    """A status in any spelling the rules or a contract use, as the enum value."""
    v = (value or "").strip()
    return PRODUCT_STATUS_ALIASES.get(v.lower(), v)


class Product(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    sku: str
    product_name_en: str | None = None
    product_name_native: str | None = None  # the name in the label's own language; the English one on English labels
    category: str | None = None
    subcategory: str | None = None
    variant: str | None = None
    form: str | None = None
    unit_size: str | None = None
    net_content: str | None = None
    description_en: str | None = None
    case_pack: int | None = None
    case_price: float | None = None
    moq_units: int | None = None
    cases_per_layer: int | None = None
    layers_per_pallet: int | None = None
    cases_per_pallet: int | None = None
    case_weight: float | None = None
    product_status: ProductStatus = "正品"
    policy_tags: list[str] = Field(default_factory=list)
    restricted_territories: list[str] = Field(default_factory=list)

    @property
    def display_name(self) -> str:
        return self.product_name_en or self.product_name_native or self.sku

    @field_validator("product_status", mode="before")
    @classmethod
    def _status_alias(cls, v: Any) -> Any:
        return product_status(v) if isinstance(v, str) else v


class CommercialRule(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    rule_type: str
    rule_config: dict[str, Any] = Field(default_factory=dict)
    severity: Literal["warn", "approval_required", "block"] = "block"
    is_active: bool = True
    partner_scope: Literal["all", "specific", "region"] = "all"
    partner_ids: list[str] = Field(default_factory=list)
    partner_regions: list[str] = Field(default_factory=list)
    product_scope: Literal["all", "specific", "role", "tag"] = "all"
    product_ids: list[str] = Field(default_factory=list)
    product_roles: list[str] = Field(default_factory=list)
    product_tags: list[str] = Field(default_factory=list)
    effective_from: str | None = None  # YYYY-MM-DD, inclusive
    effective_until: str | None = None
