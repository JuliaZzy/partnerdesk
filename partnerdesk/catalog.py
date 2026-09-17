"""The product catalog as the agent sees it: an index for resolution and a manifest for
the prompt.

Every visible product is indexed (by id and by lowercased SKU — the id the model echoes
reliably; it mis-copies UUIDs). Only the first MAX_PRODUCTS_IN_MANIFEST lines go into the
prompt. Product text is sanitized: a brand-controlled name must not be able to inject a
signal line into the prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import Product

MAX_PRODUCTS_IN_MANIFEST = 400

_SIGNAL = re.compile(r"\[(?:FILE|PO)\]", re.IGNORECASE)


def sanitize(s: str | None) -> str:
    return re.sub(r"\s+", " ", _SIGNAL.sub("", s or "").replace("|||", " / ")).strip()


def first_non_empty(*vals: str | None) -> str:
    for v in vals:
        if v and str(v).strip():
            return str(v).strip()
    return ""


@dataclass
class Catalog:
    products: list[Product]
    by_id: dict[str, Product] = field(init=False)
    by_sku: dict[str, Product] = field(init=False)

    def __post_init__(self) -> None:
        self.by_id = {p.id: p for p in self.products}
        self.by_sku = {p.sku.lower(): p for p in self.products if p.sku}

    def manifest(self) -> str:
        """One line per product: `SKU ▸ name (native) ▸ size variant ▸ pack, price, MOQ, category ▸ logistics`."""
        lines: list[str] = []
        for p in self.products[:MAX_PRODUCTS_IN_MANIFEST]:
            name = sanitize(first_non_empty(p.product_name_en, p.native_name, p.sku))
            native = f" ({sanitize(p.native_name)})" if p.native_name and p.native_name != p.product_name_en else ""
            attrs = " ".join(x for x in (sanitize(first_non_empty(p.unit_size, p.net_content)), sanitize(p.variant)) if x)
            pack = f"{p.case_pack}/case" if p.case_pack else "?/case"
            price = f"${p.case_price:.2f}/case" if p.case_price is not None else "price n/a"
            moq = f", MOQ {p.moq_units}u" if p.moq_units is not None else ""
            cat = f", {sanitize(p.category)}" if p.category else ""
            logi: list[str] = []
            if p.cases_per_layer:
                logi.append(f"{p.cases_per_layer} case/layer")
                if p.case_pack:
                    logi.append(f"{p.cases_per_layer * p.case_pack} unit/layer")
            if p.layers_per_pallet:
                logi.append(f"{p.layers_per_pallet} layer/pallet")
            if p.cases_per_pallet:
                logi.append(f"{p.cases_per_pallet} case/pallet")
            if p.case_weight is not None:
                logi.append(f"{p.case_weight:g}kg/case")
            logi_s = f" ▸ {', '.join(logi)}" if logi else ""
            lines.append(f"{sanitize(p.sku) or '(no SKU)'} ▸ {name}{native}{' ▸ ' + attrs if attrs else ''} ▸ {pack}, {price}{moq}{cat}{logi_s}")
        if len(self.products) > len(lines):
            lines.append(f"(+{len(self.products) - len(lines)} more products not listed — they can still be ordered by SKU)")
        return "\n".join(lines)

    def resolver_rows(self) -> list[dict[str, str]]:
        """What the LLM resolver reasons over — every attribute a person might name."""
        return [
            {
                "sku": p.sku, "name": p.product_name_en or "", "native": p.native_name or "",
                "unit_size": p.unit_size or p.net_content or "", "variant": p.variant or "", "category": p.category or "",
            }
            for p in self.products if p.sku
        ]
