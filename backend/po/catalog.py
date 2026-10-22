"""代理看到的产品目录：一份用于解析的索引，以及放进提示词的清单。

每个可见产品都建了索引（按 id，以及按小写 SKU——模型能稳定复述这个 id；UUID 它会抄错）。
提示词里只放前 MAX_PRODUCTS_IN_MANIFEST 行。产品文字会先消毒：品牌控制的名称不能往提示词里注入一行信号。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..schemas import Product

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
        """One line per product: `SKU ▸ name (native) ▸ size variant ▸ pack, price, MOQ, category ▸ logistics`,
        plus `▸ status` for anything that isn't a regular item (赠品 / 样品 / 停产) — the model has to
        know a gift or a discontinued line when it sees one; the rules still decide what that means."""
        lines: list[str] = []
        for p in self.products[:MAX_PRODUCTS_IN_MANIFEST]:
            name = sanitize(first_non_empty(p.product_name_en, p.product_name_native, p.sku))
            native = f" ({sanitize(p.product_name_native)})" if p.product_name_native and p.product_name_native != p.product_name_en else ""
            attrs = " ".join(x for x in (sanitize(first_non_empty(p.unit_size, p.net_content)), sanitize(p.variant)) if x)
            pack = f"{p.case_pack}/箱" if p.case_pack else "?/箱"
            price = f"${p.case_price:.2f}/箱" if p.case_price is not None else "无价格"
            moq = f"，起订量 {p.moq_units} 件" if p.moq_units is not None else ""
            cat = f"，{sanitize(p.category)}" if p.category else ""
            logi: list[str] = []
            if p.cases_per_layer:
                logi.append(f"{p.cases_per_layer} 箱/层")
                if p.case_pack:
                    logi.append(f"{p.cases_per_layer * p.case_pack} 件/层")
            if p.layers_per_pallet:
                logi.append(f"{p.layers_per_pallet} 层/托")
            if p.cases_per_pallet:
                logi.append(f"{p.cases_per_pallet} 箱/托")
            if p.case_weight is not None:
                logi.append(f"{p.case_weight:g} 千克/箱")
            logi_s = f" ▸ {', '.join(logi)}" if logi else ""
            status = f" ▸ {p.product_status}" if p.product_status != "正品" else ""
            lines.append(f"{sanitize(p.sku) or '（无 SKU）'} ▸ {name}{native}{' ▸ ' + attrs if attrs else ''} ▸ {pack}，{price}{moq}{cat}{logi_s}{status}")
        if len(self.products) > len(lines):
            lines.append(f"（另有 {len(self.products) - len(lines)} 个产品未列出——仍可按 SKU 下单）")
        return "\n".join(lines)

    def resolver_rows(self) -> list[dict[str, str]]:
        """What the LLM resolver reasons over — every attribute a person might name."""
        return [
            {
                "sku": p.sku, "name": p.product_name_en or "", "native": p.product_name_native or "",
                "unit_size": p.unit_size or p.net_content or "", "variant": p.variant or "", "category": p.category or "",
            }
            for p in self.products if p.sku
        ]
