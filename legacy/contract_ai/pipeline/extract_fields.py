"""
Contract field extraction — recall relevant chunks, then LLM → structured draft.

Output shape (`ContractExtractionDraft`) is the contract for downstream use; Node `analyze-contract`
should be updated to match when the Python engine is wired in.

Environment:
  CONTRACT_EXTRACT_PROVIDER — 'ollama' or 'openai'. Auto-detected if unset:
    Replit (REPL_ID present) → openai; local dev → ollama.
  OLLAMA_BASE_URL (default: http://localhost:11434/v1) — Ollama endpoint (local only)
  OPENAI_API_KEY or AI_INTEGRATIONS_OPENAI_API_KEY — required when provider=openai
  OPENAI_BASE_URL or AI_INTEGRATIONS_OPENAI_BASE_URL (optional)
  CONTRACT_EXTRACT_MODEL — override model name; defaults to llama3.2:3b (ollama) or gpt-4.1 (openai)
  CONTRACT_EXTRACT_MAX_CONTEXT_CHARS (default: 28000)
  CONTRACT_RECALL_MAX_CHUNKS (per group, default: 8)
  CONTRACT_EXTRACT_APPEND_FULL_TEXT (default: 1) — after keyword recall, append document tail
    (targets/schedules often at end; also avoids missing chunks with no keyword hit).
  CONTRACT_OCR_MAX_PAGES — unset/0/all = OCR full PDF; set N to cap pages.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Literal
import httpx

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..schemas import NormalizedChunk, NormalizedDocument
from .chunking import _linearize_pages

# --- Output shape (align with Node `partnerships.ts` analyze-contract JSON) ---


class FieldEvidenceEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    page: int | None = None
    quote: str = ""
    section_hint: str | None = None
    # Resolved in `full_text` / linearized page text (same string as chunking); optional from LLM.
    char_start: int | None = None
    char_end: int | None = None
    chunk_index: int | None = None


class DiscountTierBand(BaseModel):
    """One band in a tiered discount (e.g. containers 3–4 → 5% off)."""

    model_config = ConfigDict(extra="ignore")

    from_container: int | None = Field(
        None,
        description="1-based index of first unit in this band (e.g. 3 for third container)",
    )
    to_container: int | None = Field(
        None,
        description="Inclusive end; omit or null for 'and all further containers'",
    )
    discount_percent: float | None = Field(
        None,
        description="Numeric percent off (e.g. 5 for 5%); 0 means no discount for that band",
    )
    notes: str | None = None

    @field_validator("from_container", "to_container", mode="before")
    @classmethod
    def _coerce_int(cls, v: Any) -> int | None:
        if v is None or v == "":
            return None
        if isinstance(v, bool):
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, float):
            return int(v)
        if isinstance(v, str):
            s = v.strip().replace(",", "")
            try:
                return int(float(s))
            except ValueError:
                return None
        return None

    @field_validator("discount_percent", mode="before")
    @classmethod
    def _coerce_pct(cls, v: Any) -> float | None:
        if v is None or v == "":
            return None
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            s = v.strip().replace("%", "").replace(",", "")
            try:
                return float(s)
            except ValueError:
                return None
        return None


class CommercialDiscountRule(BaseModel):
    """
    Structured commercial discount (volume tiers, shipping units, rebates).
    Example: Sec 5.01(b) — each contract year, 1st–2nd containers list/base; 3rd–4th 5%; 5+ 10%.
    """

    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    section_reference: str | None = None
    applies_per: str | None = Field(
        None,
        description='e.g. "each_contract_year", "calendar_year", "per_order"',
    )
    basis: str | None = Field(
        None,
        description='e.g. "shipping_container_sequence", "purchase_volume", "other"',
    )
    tiers: list[DiscountTierBand] = Field(default_factory=list)
    source_clause_text: str | None = None

    @field_validator("tiers", mode="before")
    @classmethod
    def _coerce_tiers(cls, v: Any) -> list[Any]:
        if v is None:
            return []
        if not isinstance(v, list):
            return []
        return v


class ProductPriceEntry(BaseModel):
    """
    Per-product / per-SKU pricing line. Captures product-level prices, price-list references,
    and product-specific discounts (e.g. "10% off SKU XYZ when ordering ≥ N cases").
    Distinct from `CommercialDiscountRule` which models contract-wide volume/container tiers.
    """

    model_config = ConfigDict(extra="ignore")

    product_name: str | None = Field(
        None, description="Product / SKU name as written in the contract"
    )
    product_sku: str | None = Field(
        None, description="SKU code / item number if stated separately from the name"
    )
    unit_price: float | None = Field(
        None, description="Numeric unit price, no currency symbol or commas (e.g. 12.5)"
    )
    currency: str | None = Field(
        None, description='Currency code or symbol for unit_price (e.g. "USD", "$")'
    )
    unit: str | None = Field(
        None,
        description='Unit the price applies to (e.g. "per piece", "per case", "per container")',
    )
    list_price_reference: str | None = Field(
        None,
        description='External price-list reference if the contract defers to one (e.g. "Exhibit A", "Brand Price List dated 2026-01-01")',
    )
    product_specific_discount_text: str | None = Field(
        None,
        description='Verbatim product-specific discount clause (e.g. "10% off SKU XYZ when ordering 5+ cases"). Leave null if discount is contract-wide.',
    )
    source_clause_text: str | None = None

    @field_validator("unit_price", mode="before")
    @classmethod
    def _coerce_unit_price(cls, v: Any) -> float | None:
        if v is None or v == "":
            return None
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            s = v.strip().replace(",", "").replace("$", "").replace("USD", "").strip()
            try:
                return float(s)
            except ValueError:
                return None
        return None


class MinimumOrderQuantity(BaseModel):
    """
    Minimum Order Quantity (MOQ) — the smallest order/shipment the distributor may place.
    Distinct from `annual_sales_target` which is a yearly purchase commitment in money.
    """

    model_config = ConfigDict(extra="ignore")

    quantity: float | None = Field(
        None, description="Numeric MOQ value, no units (e.g. 100)"
    )
    unit: str | None = Field(
        None,
        description='Unit of quantity (e.g. "pieces", "cases", "containers", "kg", "USD"). Use "USD"/"CNY" only when MOQ is expressed in monetary value, not pieces.',
    )
    applies_per: str | None = Field(
        None,
        description='Scope of the minimum: "per_order", "per_shipment", "per_month", "per_quarter", "per_year", or "other"',
    )
    product_scope: str | None = Field(
        None,
        description='Which products this MOQ covers; null/"all" if it applies to every product',
    )
    source_clause_text: str | None = None

    @field_validator("quantity", mode="before")
    @classmethod
    def _coerce_quantity(cls, v: Any) -> float | None:
        if v is None or v == "":
            return None
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            s = v.strip().replace(",", "")
            try:
                return float(s)
            except ValueError:
                return None
        return None


class ContractExtractionDraft(BaseModel):
    """Structured extraction result (JSON-serializable). Obligation dicts may include `responsible_party`."""

    model_config = ConfigDict(extra="ignore")

    contract_type: str | None = None
    effective_date: str | None = Field(
        None,
        description="Prefer explicit contract/term start date in the body; if none, use latest signing/execution date.",
    )
    term_start_date: str | None = None
    term_end_date: str | None = None
    auto_renewal: bool | None = None
    renewal_term_length_text: str | None = None
    renewal_notice_days: int | None = None
    exclusivity_type: str | None = None
    territory_text: str | None = None
    allowed_countries: list[str] | None = None
    excluded_countries: list[str] | None = None
    allowed_regions_text: str | None = None
    territory_restrictions_text: str | None = None
    sales_rights_text: str | None = None
    reporting_requirements_text: str | None = None
    required_submissions_text: str | None = None
    key_deadlines_text: str | None = None
    governing_language: str | None = None
    # Year label → amount, e.g. {"2026": 400000, "2027": 600000} (JSON keys are strings).
    annual_sales_target: dict[str, float] | None = None
    # One currency for all amounts: e.g. "USD", "$", "美金".
    annual_sales_target_currency: str | None = None
    # Tiered discounts, rebates, shipping-container sequence pricing (see CommercialDiscountRule).
    commercial_discount_rules: list[CommercialDiscountRule] | None = None
    # Verbatim clause(s) on pricing/discounts when structured tiers are unclear or absent — portable across contracts.
    commercial_discount_clause_text: str | None = None
    # Per-product / per-SKU prices (separate from contract-wide discount tiers).
    product_price_list: list[ProductPriceEntry] | None = None
    # Verbatim pricing/price-list clause(s) for portability when structured rows are unclear.
    product_price_list_clause_text: str | None = None
    # Minimum Order Quantities (per_order / per_shipment / per_month etc.).
    minimum_order_quantities: list[MinimumOrderQuantity] | None = None
    # Open-ended business rules: payment, ordering/shipping, brand-use, marketing, compliance, etc.
    # Free-form dicts (like `obligations`) so downstream mapping can evolve without schema churn.
    business_rules: list[dict[str, Any]] = Field(default_factory=list)
    confidence_score: float | None = Field(None, ge=0.0, le=1.0)
    extraction_notes: str | None = None

    field_evidence: dict[str, list[FieldEvidenceEntry]] = Field(default_factory=dict)
    obligations: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("product_price_list", mode="before")
    @classmethod
    def coerce_product_price_list(cls, v: Any) -> list[ProductPriceEntry] | None:
        if v is None:
            return None
        if not isinstance(v, list):
            return None
        out: list[ProductPriceEntry] = []
        for item in v:
            if not isinstance(item, dict):
                continue
            try:
                out.append(ProductPriceEntry.model_validate(item))
            except Exception:
                continue
        return out if out else None

    @field_validator("minimum_order_quantities", mode="before")
    @classmethod
    def coerce_minimum_order_quantities(cls, v: Any) -> list[MinimumOrderQuantity] | None:
        if v is None:
            return None
        if not isinstance(v, list):
            return None
        out: list[MinimumOrderQuantity] = []
        for item in v:
            if not isinstance(item, dict):
                continue
            try:
                out.append(MinimumOrderQuantity.model_validate(item))
            except Exception:
                continue
        return out if out else None

    @field_validator("business_rules", mode="before")
    @classmethod
    def coerce_business_rules(cls, v: Any) -> list[dict[str, Any]]:
        if v is None:
            return []
        if not isinstance(v, list):
            return []
        return [item for item in v if isinstance(item, dict)]

    @field_validator("annual_sales_target", mode="before")
    @classmethod
    def coerce_annual_sales_target_map(cls, v: Any) -> dict[str, float] | None:
        if v is None:
            return None
        if not isinstance(v, dict):
            return None
        out: dict[str, float] = {}
        for k, raw in v.items():
            key = str(k).strip()
            if not key:
                continue
            if isinstance(raw, bool):
                continue
            if isinstance(raw, (int, float)):
                out[key] = float(raw)
                continue
            if isinstance(raw, str):
                s = raw.replace(",", "").replace(" ", "").strip()
                if not s:
                    continue
                try:
                    out[key] = float(s)
                except ValueError:
                    continue
        return out if out else None

    @field_validator("commercial_discount_rules", mode="before")
    @classmethod
    def coerce_commercial_discount_rules(
        cls, v: Any
    ) -> list[CommercialDiscountRule] | None:
        if v is None:
            return None
        if not isinstance(v, list):
            return None
        out: list[CommercialDiscountRule] = []
        for item in v:
            if not isinstance(item, dict):
                continue
            try:
                out.append(CommercialDiscountRule.model_validate(item))
            except Exception:
                tiers: list[DiscountTierBand] = []
                for t in item.get("tiers") or []:
                    if isinstance(t, dict):
                        try:
                            tiers.append(DiscountTierBand.model_validate(t))
                        except Exception:
                            pass
                out.append(
                    CommercialDiscountRule(
                        title=str(item.get("title", "")) or None,
                        section_reference=item.get("section_reference")
                        if isinstance(item.get("section_reference"), str)
                        else None,
                        applies_per=item.get("applies_per")
                        if isinstance(item.get("applies_per"), str)
                        else None,
                        basis=item.get("basis") if isinstance(item.get("basis"), str) else None,
                        tiers=tiers,
                        source_clause_text=item.get("source_clause_text")
                        if isinstance(item.get("source_clause_text"), str)
                        else None,
                    )
                )
        return out if out else None


FieldGroup = Literal[
    "renewal_terms",
    "territory_exclusivity",
    "commercial_targets",
    "obligations_reporting",
    "business_rules",
]


FIELD_GROUP_KEYWORDS: dict[str, list[str]] = {
    # Listed first: build_recall_context_for_llm truncates recall_body to recall_cap; if
    # commercial_targets were after renewal+territory, tier/discount clauses were dropped entirely.
    # Also recalls product price list and MOQ context — both downstream-extracted from this group.
    "commercial_targets": [
        "sales target",
        "minimum purchase",
        "minimum",
        "commitment",
        "quota",
        "wholesale",
        "annual",
        "year",
        "usd",
        "discount",
        "rebate",
        "tier",
        "container",
        "containers",
        "shipping container",
        "price reduction",
        "less than the price",
        "less than the price list",
        "pricing",
        # Product price list / per-SKU pricing
        "price list",
        "unit price",
        "list price",
        "per unit",
        "per case",
        "per piece",
        "per bottle",
        "per carton",
        "sku",
        "schedule of prices",
        "exhibit a",
        "exhibit b",
        "appendix",
        "schedule",
        "product price",
        # MOQ
        "minimum order quantity",
        "moq",
        "minimum order",
        "minimum quantity",
        "minimum shipment",
        "per order",
        "per shipment",
        "销售目标",
        "最低采购",
        "最低销售",
        "采购承诺",
        "最低",
        "承诺",
        "万美元",
        "折扣",
        "货柜",
        "集装箱",
        "价目表",
        "单价",
        "每箱",
        "每件",
        "每瓶",
        "最低订货",
        "最低订单",
        "最少订购",
        "起订量",
    ],
    "renewal_terms": [
        "renew",
        "renewal",
        "notice",
        "termination",
        "expire",
        "extension",
        "effective date",
        "effective",
        "execution",
        "signature",
        "executed",
        "续约",
        "通知",
        "终止",
        "生效",
        "签署",
    ],
    "territory_exclusivity": [
        "territory",
        "region",
        "exclusive",
        "non-exclusive",
        "地域",
        "区域",
        "排他",
        "独家",
    ],
    "obligations_reporting": [
        "report",
        "reports",
        "submit",
        "deadline",
        "quarterly",
        "monthly",
        "forecast",
        "sales report",
        "ship date",
        "prior to",
        "marketing",
        "materials",
        "provide",
        "shipment",
        "shipping",
        "报告",
        "提交",
        "期限",
        "宣传",
        "材料",
        "发货",
        "月报",
        "预测",
    ],
    # Generic operational / policy rules — payment terms, ordering process, brand-use, IP,
    # compliance, confidentiality, warranties, returns, etc. Captured into `business_rules`.
    "business_rules": [
        # Payment
        "payment terms",
        "shall pay",
        "net 30",
        "net 60",
        "net 90",
        "letter of credit",
        "l/c",
        "wire transfer",
        "t/t",
        "telegraphic transfer",
        "credit terms",
        "deposit",
        "invoice",
        "late payment",
        "interest",
        # Ordering / shipping
        "purchase order",
        "incoterms",
        "fob",
        "cif",
        "exw",
        "ddp",
        "dap",
        "lead time",
        "delivery",
        "title passes",
        "risk of loss",
        "inspection",
        "acceptance",
        "reject",
        # Brand / marketing / IP
        "trademark",
        "intellectual property",
        "brand guidelines",
        "marketing materials",
        "promotional",
        "advertising",
        "co-branding",
        "approval",
        "social media",
        "domain name",
        "logo",
        "trade dress",
        # Compliance / legal
        "anti-bribery",
        "anti-corruption",
        "fcpa",
        "sanctions",
        "export control",
        "confidential",
        "confidentiality",
        "non-disclosure",
        "data protection",
        "gdpr",
        "personal data",
        "indemnify",
        "indemnification",
        "warrant",
        "warranty",
        "liability",
        "insurance",
        "force majeure",
        "governing law",
        "dispute",
        "arbitration",
        "audit",
        "compliance",
        # Returns / consumer
        "return",
        "returns",
        "recall",
        "defective",
        "consumer complaint",
        "customer complaint",
        # Chinese
        "付款条件",
        "付款方式",
        "电汇",
        "信用证",
        "订单",
        "交货",
        "保密",
        "知识产权",
        "商标",
        "宣传材料",
        "合规",
        "反贿赂",
        "数据保护",
        "投诉",
        "退货",
        "保修",
        "保证",
        "赔偿",
        "不可抗力",
        "争议",
        "仲裁",
        "审计",
        "管辖法律",
    ],
}


def _chunks_usable(doc: NormalizedDocument) -> list[NormalizedChunk]:
    if doc.derived.chunks:
        return doc.derived.chunks
    out: list[NormalizedChunk] = []
    for p in doc.pages:
        t = p.page_text()
        if not t.strip():
            continue
        out.append(
            NormalizedChunk(
                chunk_index=len(out),
                text=t,
                page_start=p.page_number,
                page_end=p.page_number,
                section_hint=None,
            )
        )
    if not out and (doc.full_text or "").strip():
        out.append(
            NormalizedChunk(
                chunk_index=0,
                text=doc.full_text.strip(),
                page_start=1,
                page_end=max(len(doc.pages), 1),
            )
        )
    return out


def recall_chunks_for_field_group(
    doc: NormalizedDocument,
    group: str,
    *,
    max_chunks: int | None = None,
) -> list[NormalizedChunk]:
    cap = max_chunks if max_chunks is not None else int(os.environ.get("CONTRACT_RECALL_MAX_CHUNKS", "8"))
    if group not in FIELD_GROUP_KEYWORDS:
        raise ValueError(f"Unknown field group {group!r}. Use one of: {list(FIELD_GROUP_KEYWORDS)}")
    keywords = FIELD_GROUP_KEYWORDS[group]

    chunks = _chunks_usable(doc)
    scored: list[tuple[int, NormalizedChunk]] = []
    for ch in chunks:
        tl = ch.text.lower()
        score = sum(1 for kw in keywords if kw.lower() in tl)
        if score > 0:
            scored.append((score, ch))
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:cap]]


def _dedupe_chunks(chunks: list[NormalizedChunk]) -> list[NormalizedChunk]:
    seen: set[int] = set()
    out: list[NormalizedChunk] = []
    for ch in chunks:
        if ch.chunk_index in seen:
            continue
        seen.add(ch.chunk_index)
        out.append(ch)
    return out


def build_recall_context_for_llm(doc: NormalizedDocument) -> str:
    """
    Keyword recall per group, then (by default) append the **tail** of the full document.
    Schedules / minimum purchase tables often sit at the end; recall can also miss chunks
    that do not contain any keyword substring.
    """
    max_chars = int(os.environ.get("CONTRACT_EXTRACT_MAX_CONTEXT_CHARS", "28000"))
    append_full = os.environ.get("CONTRACT_EXTRACT_APPEND_FULL_TEXT", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )

    all_chunks: list[NormalizedChunk] = []
    for group in FIELD_GROUP_KEYWORDS:
        all_chunks.extend(recall_chunks_for_field_group(doc, group, max_chunks=6))
    all_chunks = _dedupe_chunks(all_chunks)

    sections: list[str] = []
    if all_chunks:
        for group in FIELD_GROUP_KEYWORDS:
            group_chunks = recall_chunks_for_field_group(doc, group, max_chunks=6)
            if not group_chunks:
                continue
            parts = []
            for ch in group_chunks:
                label = f"[pages {ch.page_start}-{ch.page_end}]"
                if ch.section_hint:
                    label += f" section≈{ch.section_hint[:80]!r}"
                parts.append(f"{label}\n{ch.text}")
            sections.append(f"### Recall group: {group}\n" + "\n\n".join(parts))
        recall_body = "\n\n---\n\n".join(sections)
    else:
        recall_body = ""

    flat = doc.flattened_text() or ""

    if not recall_body.strip():
        body = flat
    elif append_full and flat.strip():
        marker = (
            "\n\n---\n\n### Full document tail (same contract; targets/schedules and signature/execution dates often here)\n"
        )
        overhead = len(marker) + 40
        # Cap recall so tail always gets space; keep high enough that early groups (e.g.
        # commercial_targets) are not truncated before container/discount clauses (~21k+).
        recall_cap = min(24000, max_chars - 4000)
        rb = recall_body[:recall_cap] + ("...[recall truncated]\n" if len(recall_body) > recall_cap else "")
        tail_budget = max_chars - len(rb) - overhead
        if tail_budget < 2000:
            tail_budget = max(0, max_chars - len(recall_body) - overhead)
        if tail_budget <= 0:
            body = rb
        elif len(flat) <= tail_budget:
            body = rb + marker + flat
        else:
            body = rb + marker + flat[-tail_budget:]
    else:
        body = recall_body

    if len(body) > max_chars:
        body = body[: max_chars - 20] + "\n...[truncated]"
    return body


def _get_llm_provider() -> str:
    """Return 'ollama' for local dev, 'openai' for Replit/prod."""
    explicit = os.environ.get("CONTRACT_EXTRACT_PROVIDER", "").strip().lower()
    if explicit in ("ollama", "openai"):
        return explicit
    return "openai" if "REPL_ID" in os.environ else "ollama"


def _get_openai_client() -> OpenAI:
    provider = _get_llm_provider()

    if provider == "ollama":
        base_url = os.environ.get("OLLAMA_BASE_URL", "").strip()
        if not base_url:
            base_url = os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL", "").strip()
        if not base_url:
            base_url = "http://localhost:11434/v1"
        base_url = base_url.rstrip("/")

        api_key = os.environ.get("OLLAMA_API_KEY", "").strip()
        if not api_key:
            api_key = os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY", "").strip()
        if not api_key:
            api_key = "ollama"

        return OpenAI(api_key=api_key, base_url=base_url)

    # OpenAI path (Replit or prod)
    on_replit = "REPL_ID" in os.environ
    if on_replit:
        key = os.environ.get("AI_INTEGRATIONS_OPENAI_API_KEY")
        if not key:
            raise RuntimeError(
                "Set AI_INTEGRATIONS_OPENAI_API_KEY for contract field extraction on Replit."
            )
        base = os.environ.get("AI_INTEGRATIONS_OPENAI_BASE_URL")
        kw: dict[str, Any] = {"api_key": key}
        if base and base.strip():
            kw["base_url"] = base.strip()
    else:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(
                "Set OPENAI_API_KEY in the repo root .env for contract field extraction."
            )
        base = os.environ.get("OPENAI_BASE_URL")
        kw = {"api_key": key}
        if base and base.strip():
            kw["base_url"] = base.strip()
        proxy_url = (
            os.environ.get("HTTP_PROXY")
            or os.environ.get("http_proxy")
            or os.environ.get("HTTPS_PROXY")
            or os.environ.get("https_proxy")
            or "http://127.0.0.1:7897"
        )
        try:
            from httpx_socks import SyncProxyTransport
            transport = SyncProxyTransport.from_url(proxy_url)
            http_client = httpx.Client(transport=transport)
            kw["http_client"] = http_client
        except Exception as e:
            print(f"Warning: Failed to set HTTP proxy using httpx_socks: {e}")
            try:
                http_client = httpx.Client(proxy=proxy_url)
                kw["http_client"] = http_client
            except Exception as e2:
                print(f"Warning: Failed to set HTTP proxy using httpx.Client: {e2}")

    return OpenAI(**kw)


_EXTRACTION_SYSTEM_RULES = """You are a contract intelligence extraction system. Extract structured data from the PROVIDED TEXT (keyword recall + full document tail when present). The excerpt may be partial if the source PDF was OCR-limited to early pages.

Rules:
- Extract only terms explicitly stated or very strongly inferable from the provided text.
- Do NOT invent dates, countries, obligations, or terms not supported by the excerpts.
- If a field is uncertain or missing, use null.
- **Effective date / term start:** **Priority for effective_date:** (1) If the contract states an **explicit calendar start** (or a clear start/end period with a **start date**), use that **start date** for **effective_date** (e.g. "Effective Date: January 1, 2026", "本合同期限自 2026年1月1日 起至 …", "Term from … to …"). (2) If there is **no** such explicit start/end or effective calendar date in the excerpts, then use the **latest signing / execution / countersignature date** (often on the last pages: "Executed on", "Date:", 签署日期, or dates beside signatures). The body may still say "from the Effective Date" without a number; only then rely on the signature block. If the initial term is defined as beginning on the effective date, set **term_start_date** to match **effective_date** unless another start date is explicit.
- For obligations: each distinct obligation, deadline, reporting requirement, marketing/support commitment, shipping-related requirement, or termination-related item = one array entry (split distinct duties into separate rows).
- **obligation_type `task`**: use for **one concrete operational deliverable** with a clear cadence or trigger (e.g. monthly sales report within N days after month-end; written forecast/report X days before each shipment). **Split** compound reporting clauses into **multiple rows** — e.g. "Sales Reports/Forecasts" with both month-end reporting **and** pre-ship reporting → **two** separate obligations with `obligation_type` **task**, not one vague "report" row.
- **obligation_type `report`**: broader standing reporting duty when the text does not separate distinct timings; prefer **`task`** when the contract gives separate deadlines (monthly vs before ship date).
- **obligation_type `decision_needed`**: use when the clause requires an **explicit approval, election, or mutual decision** (not a routine submission, report, or payment).
- For each obligation set responsible_party: who must perform or provide — "brand" (Company/Brand/Vendor/甲方), "distributor" (Distributor/Dealer/经销商/代理商), "both", or "unknown" if unclear.
- For source_clause_text on obligations: quote the supporting sentence(s) from the excerpts.
- For field_evidence: for important scalar fields you fill, include 1–3 short quotes with page numbers if inferable from [pages X–Y] markers.
- For **commercial_discount_rules**: when the contract clearly states tiered or banded pricing (containers, volume, rebates), fill structured **tiers**; otherwise use **null** for that array.
- For **commercial_discount_clause_text**: when any clause discusses price lists, discounts, rebates, or container-based pricing, copy the **verbatim English (or primary) sentence(s)** from the excerpts here. **Always prefer this** if structured tiers are ambiguous — a quoted clause is enough for downstream review.
- For **product_price_list**: extract per-product / per-SKU prices when the contract lists named products with unit prices (e.g. price tables, "Exhibit A" line items, or sentences like "Product XYZ shall be sold at USD 12.50 per case"). One row per product. If only an external reference like "see Exhibit A" exists with no actual prices in the excerpts, set `list_price_reference` and leave `unit_price` null. **product_specific_discount_text** is for discounts tied to a specific product (e.g. "10% off SKU XYZ when ordering 5+ cases") — do NOT duplicate contract-wide tiered discounts here.
- For **product_price_list_clause_text**: verbatim quote of any clause introducing pricing or referring to a price list (portable fallback when structured rows are unclear).
- For **minimum_order_quantities (MOQ)**: extract every minimum order / minimum shipment / minimum quantity rule. One row per distinct rule. `quantity` is numeric only; put the unit string in `unit` (e.g. "cases", "containers", "pieces", "USD" if the minimum is monetary). Set `applies_per` to the scope (per_order, per_shipment, per_month, per_quarter, per_year, other). MOQ is **distinct** from `annual_sales_target` — do not put yearly purchase commitments here.
- For **business_rules**: extract all operational / policy rules NOT already captured as obligations (which carry deadlines or triggers). Examples: payment terms, ordering procedure, Incoterms / delivery terms, brand & trademark usage rules, marketing material approval rules, confidentiality, IP, anti-bribery / compliance, data protection, warranties, returns, indemnification, force majeure, governing law, dispute resolution, audit rights. One rule per array entry — **split** compound clauses. Use the same `responsible_party` convention as obligations ("brand", "distributor", "both", "unknown"). If a clause is already covered by an obligation entry, prefer to keep it in `obligations`; only duplicate if the rule has both a recurring duty AND a standing policy aspect.

Return ONLY valid JSON, no markdown fences."""


def _extraction_json_schema_instruction() -> str:
    return """
Return a single JSON object with these keys (use null where unknown):
{
  "contract_type": string | null,
  "effective_date": "YYYY-MM-DD" | null,
  "term_start_date": "YYYY-MM-DD" | null,
  "term_end_date": "YYYY-MM-DD" | null,
  "auto_renewal": boolean | null,
  "renewal_term_length_text": string | null,
  "renewal_notice_days": number | null,
  "exclusivity_type": "exclusive" | "non-exclusive" | "conditional-exclusive" | "unknown" | null,
  "territory_text": string | null,
  "allowed_countries": string[] | null,
  "excluded_countries": string[] | null,
  "allowed_regions_text": string | null,
  "territory_restrictions_text": string | null,
  "sales_rights_text": string | null,
  "reporting_requirements_text": string | null,
  "required_submissions_text": string | null,
  "key_deadlines_text": string | null,
  "governing_language": string | null,
  "annual_sales_target": { "2026": number, "2027": number, ... } | null,
  "annual_sales_target_currency": string | null,
  "commercial_discount_clause_text": string | null,
  "commercial_discount_rules": [
    {
      "title": string | null,
      "section_reference": string | null,
      "applies_per": "each_contract_year" | "calendar_year" | "per_order" | string | null,
      "basis": "shipping_container_sequence" | "purchase_volume" | "other" | string | null,
      "tiers": [
        {
          "from_container": number | null,
          "to_container": number | null,
          "discount_percent": number | null,
          "notes": string | null
        }
      ],
      "source_clause_text": string | null
    }
  ] | null,
  "product_price_list_clause_text": string | null,
  "product_price_list": [
    {
      "product_name": string | null,
      "product_sku": string | null,
      "unit_price": number | null,
      "currency": string | null,
      "unit": string | null,
      "list_price_reference": string | null,
      "product_specific_discount_text": string | null,
      "source_clause_text": string | null
    }
  ] | null,
  "minimum_order_quantities": [
    {
      "quantity": number | null,
      "unit": string | null,
      "applies_per": "per_order" | "per_shipment" | "per_month" | "per_quarter" | "per_year" | "other" | null,
      "product_scope": string | null,
      "source_clause_text": string | null
    }
  ] | null,
  "business_rules": [
    {
      "rule_category": "payment" | "ordering" | "shipping_delivery" | "brand_usage" | "marketing_approval" | "confidentiality" | "intellectual_property" | "compliance" | "data_protection" | "warranty" | "returns" | "indemnification" | "liability" | "insurance" | "force_majeure" | "governing_law" | "dispute_resolution" | "audit" | "other",
      "responsible_party": "brand" | "distributor" | "both" | "unknown" | null,
      "title": string,
      "description": string | null,
      "section_reference": string | null,
      "source_clause_text": string | null
    }
  ],
  "confidence_score": number,
  "extraction_notes": string | null,
  "obligations": [
    {
      "obligation_type": "report" | "task" | "payment" | "renewal" | "termination" | "target" | "submission" | "decision_needed" | "other",
      "responsible_party": "brand" | "distributor" | "both" | "unknown" | null,
      "title": string,
      "description": string | null,
      "deadline_type": "fixed_date" | "relative" | "recurring" | "conditional" | "none",
      "deadline_date": "YYYY-MM-DD" | null,
      "deadline_rule_text": string | null,
      "recurrence_text": string | null,
      "trigger_text": string | null,
      "territory_scope": string | null,
      "source_clause_text": string | null
    }
  ],
  "field_evidence": {
    "renewal_notice_days": [ { "page": number | null, "quote": string } ],
    "territory_text": [ { "page": number | null, "quote": string } ]
  }
}

EFFECTIVE DATE AND TERM START (critical):
- **effective_date (priority):** (1) **First** use any **explicit contract start** or **start of term** in the body (YYYY-MM-DD): stated Effective Date, "自…起", "from … to …" start, etc. (2) **Only if** no such calendar start/end or effective date appears in the excerpts, use the **last / latest signing or execution date** (signature block, "Executed as of", "Date:", 签署日期, dates beside signatures).
- Output **effective_date** as YYYY-MM-DD. If the initial term begins on the effective date, set **term_start_date** to the same date unless another start date is explicit.
- If you are using signature dates and multiple dates appear (e.g. two parties signed different days), prefer the date that establishes when the agreement was fully executed, or the latest if the contract so indicates.

SALES TARGETS (critical): Scan for minimum purchase / sales targets by calendar year or contract year.
- Set annual_sales_target to an object whose keys are year labels as STRINGS (e.g. "2026", "2027", or "Year 1", "Year 2" if no calendar year) and values are numeric amounts without commas (e.g. 400000 for $400,000).
- Example: "annual_sales_target": { "2026": 400000, "2027": 600000 }
- **Uniform per-year amount:** If the contract states one annual figure for every year (e.g. "USD 200,000 per year", "每年最低采购20万美元", "minimum of $200k each contract year") but does not list years separately, **expand** to one key per year covered by the initial term (infer years from effective/term dates when possible, else use "Year 1"…"Year N" for N years). **Use the same numeric value for every key** (e.g. five-year term at $200k/year → five entries all 200000).
- Set annual_sales_target_currency to a single code or symbol for all those amounts: e.g. "USD", "$", "美金", "CNY".
- If no targets are stated, use null for both.

DISCOUNTS / PRICING CLAUSES:
- Set **commercial_discount_clause_text** to a **verbatim quote** of the main clause(s) about list price, discounts, rebates, or container-based pricing (any contract). This field is the portable fallback; fill it whenever such language appears.
- Optionally set **commercial_discount_rules** when tiers are **clear** (ordinal containers, explicit % off bands). If bands are messy or uncertain, leave **commercial_discount_rules** null and rely on **commercial_discount_clause_text** plus per-rule **source_clause_text** when you do output tiers.

PRODUCT PRICE LIST (per-SKU pricing, separate from contract-wide discounts):
- Use **product_price_list** when the contract names individual products / SKUs with unit prices, OR contains a price table (often in an Exhibit / Appendix / Schedule). One row per product.
  - Example: "Product 'Calming Serum 30ml' shall be sold to Distributor at USD 18.50 per bottle" → `{ product_name: "Calming Serum 30ml", unit_price: 18.5, currency: "USD", unit: "per bottle", source_clause_text: "..." }`.
  - If the contract only references an external list ("Prices are as set forth in Exhibit A") and no actual numbers appear in the excerpts, output ONE row with `list_price_reference: "Exhibit A"` and `unit_price: null` rather than fabricating prices.
- **product_specific_discount_text**: per-product discount language tied to a specific SKU (e.g. "10% off SKU XYZ when ordering 5+ cases"). Do NOT use this for the contract-wide container/volume discount — that belongs in `commercial_discount_rules` / `commercial_discount_clause_text`.
- **product_price_list_clause_text**: verbatim quote of the clause introducing the price list (portable fallback).
- If no per-product pricing or price-list reference exists, set both `product_price_list` and `product_price_list_clause_text` to null.

MINIMUM ORDER QUANTITY (MOQ):
- Extract into **minimum_order_quantities** every minimum-quantity rule. One entry per distinct rule.
- `quantity` is a number only (no commas, no unit string). `unit` carries the unit: "pieces", "cases", "containers", "kg", or "USD"/"CNY" if the minimum is monetary value.
- `applies_per` is one of: "per_order", "per_shipment", "per_month", "per_quarter", "per_year", "other".
- MOQ is DIFFERENT from `annual_sales_target` — annual purchase commitments in money go into `annual_sales_target`, not here.
- Examples:
  - "Each order shall be no less than 100 cases" → `{ quantity: 100, unit: "cases", applies_per: "per_order" }`.
  - "Minimum shipment value: USD 5,000" → `{ quantity: 5000, unit: "USD", applies_per: "per_shipment" }`.
  - "起订量为一个20尺货柜" → `{ quantity: 1, unit: "20-foot container", applies_per: "per_order" }`.
- If no MOQ language is present, set the array to null.

BUSINESS RULES (general operational / policy clauses):
- Use **business_rules** for clauses that are policies or standing rules without a deadline/recurring trigger — those would go in `obligations` instead.
- Examples of categories:
  - **payment**: payment terms, currency, deposit %, late-payment interest, invoice cycle.
  - **ordering**: how POs are issued, accepted, modified, cancelled.
  - **shipping_delivery**: Incoterms (FOB/CIF/DDP), title transfer, risk of loss, lead time, inspection/acceptance.
  - **brand_usage / marketing_approval / intellectual_property**: trademark use, marketing material pre-approval, social media rules, no modification of packaging.
  - **confidentiality / data_protection**: NDA scope, surviving term, personal-data handling.
  - **compliance**: anti-bribery (FCPA/UKBA), sanctions, export control, audit rights.
  - **warranty / returns / indemnification / liability / insurance / force_majeure / governing_law / dispute_resolution**: standard contract boilerplate when explicitly written.
- Split compound clauses into multiple entries. Set `responsible_party` exactly like obligations.
- If a clause is BOTH a recurring duty AND a policy, prefer `obligations`; do not duplicate unless the rule contains a separable standing-policy part.
- Set `source_clause_text` to a verbatim quote of the supporting sentence(s).
- Aim for completeness: it is acceptable for `business_rules` to contain many entries (10–40 is normal for full contracts).

DISTRIBUTOR REPORTING — `task` vs `report` (critical):
- Search for **monthly sales reports**, **forecasts**, **Sales Reports/Forecasts** (e.g. §4.7), **prior to ship date** / **ship date of each order**.
- When the Distributor must do **two or more different things** with **different triggers or deadlines**, create **one obligation per rule** with `obligation_type` **task** and `responsible_party` **distributor**:
  - Example A: "Monthly sales reports for the previous month … within fifteen (15) [days] from the end of the month" → **task**, `deadline_type` **recurring**, `deadline_rule_text` with the timing, `title` e.g. "Monthly sales report (prior month)".
  - Example B: "Twenty (20) days prior to the ship date of each order, … written report outlining existing and upcoming sales results and expectations" → **separate** **task**, `deadline_type` **conditional** or **recurring**, `trigger_text` / `deadline_rule_text` describing **before each order ship date**, `title` e.g. "Pre-shipment sales forecast report".
- Do **not** merge these into a single generic obligation if the contract states separate timings.
"""


def _coerce_int_opt(v: Any) -> int | None:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        try:
            return int(float(s))
        except ValueError:
            return None
    return None


def _parse_field_evidence(raw: dict[str, Any]) -> dict[str, list[FieldEvidenceEntry]]:
    fe = raw.get("field_evidence")
    if not isinstance(fe, dict):
        return {}
    out: dict[str, list[FieldEvidenceEntry]] = {}
    for k, v in fe.items():
        if not isinstance(v, list):
            continue
        entries: list[FieldEvidenceEntry] = []
        for item in v:
            if isinstance(item, dict):
                entries.append(
                    FieldEvidenceEntry(
                        page=item.get("page"),
                        quote=str(item.get("quote", "") or ""),
                        section_hint=item.get("section_hint")
                        if isinstance(item.get("section_hint"), str)
                        else None,
                        char_start=_coerce_int_opt(item.get("char_start")),
                        char_end=_coerce_int_opt(item.get("char_end")),
                        chunk_index=_coerce_int_opt(item.get("chunk_index")),
                    )
                )
        if entries:
            out[str(k)] = entries
    return out


def _locate_quote_span(full_text: str, quote: str) -> tuple[int, int] | None:
    """Find a span of `quote` in `full_text` (exact, then case-insensitive; short prefix fallback)."""
    if not full_text or not quote or not quote.strip():
        return None
    q = quote.strip()
    i = full_text.find(q)
    if i >= 0:
        return (i, i + len(q))
    fl = full_text.lower()
    ql = q.lower()
    j = fl.find(ql)
    if j >= 0:
        return (j, j + len(q))
    if len(q) > 400:
        return _locate_quote_span(full_text, q[:400])
    return None


def _chunk_index_for_char(chunks: list[NormalizedChunk], pos: int) -> int | None:
    for ch in chunks:
        cs = ch.char_start
        ce = ch.char_end
        if cs is not None and ce is not None and cs <= pos < ce:
            return ch.chunk_index
    return None


def _enrich_field_evidence_spans(doc: NormalizedDocument, draft: ContractExtractionDraft) -> None:
    """Fill char_start/char_end/chunk_index by locating `quote` in linearized text (same as chunking)."""
    linear_text, _ = _linearize_pages(doc)
    if not linear_text.strip():
        return
    chunks = list(doc.derived.chunks) if doc.derived and doc.derived.chunks else []
    for _key, entries in (draft.field_evidence or {}).items():
        for e in entries:
            if e.char_start is not None and e.char_end is not None:
                continue
            span = _locate_quote_span(linear_text, e.quote)
            if span:
                e.char_start, e.char_end = span[0], span[1]
                idx = _chunk_index_for_char(chunks, span[0])
                if idx is not None:
                    e.chunk_index = idx


def _unnest_llm_extraction_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Some models nest scalars under `extraction` / `result` — merge into top-level before Pydantic."""
    out = dict(data)
    for key in ("extraction", "result", "data", "contract", "fields"):
        inner = out.get(key)
        if isinstance(inner, dict) and inner:
            for k, v in inner.items():
                cur = out.get(k)
                if cur in (None, "") and v not in (None, ""):
                    out[k] = v
    return out


_EXTRACTION_CAMEL_TO_SNAKE: dict[str, str] = {
    "contractType": "contract_type",
    "effectiveDate": "effective_date",
    "termStartDate": "term_start_date",
    "termEndDate": "term_end_date",
    "autoRenewal": "auto_renewal",
    "renewalTermLengthText": "renewal_term_length_text",
    "renewalNoticeDays": "renewal_notice_days",
    "exclusivityType": "exclusivity_type",
    "territoryText": "territory_text",
    "allowedCountries": "allowed_countries",
    "excludedCountries": "excluded_countries",
    "allowedRegionsText": "allowed_regions_text",
    "territoryRestrictionsText": "territory_restrictions_text",
    "salesRightsText": "sales_rights_text",
    "reportingRequirementsText": "reporting_requirements_text",
    "requiredSubmissionsText": "required_submissions_text",
    "keyDeadlinesText": "key_deadlines_text",
    "governingLanguage": "governing_language",
    "annualSalesTarget": "annual_sales_target",
    "annualSalesTargetCurrency": "annual_sales_target_currency",
    "salesTargetSchedule": "sales_target_schedule",
    "commercialDiscountRules": "commercial_discount_rules",
    "commercialDiscountClauseText": "commercial_discount_clause_text",
    "productPriceList": "product_price_list",
    "productPriceListClauseText": "product_price_list_clause_text",
    "minimumOrderQuantities": "minimum_order_quantities",
    "businessRules": "business_rules",
    "confidenceScore": "confidence_score",
    "extractionNotes": "extraction_notes",
    "fieldEvidence": "field_evidence",
}

_OBLIGATION_CAMEL_TO_SNAKE: dict[str, str] = {
    "obligationType": "obligation_type",
    "deadlineType": "deadline_type",
    "deadlineDate": "deadline_date",
    "deadlineRuleText": "deadline_rule_text",
    "recurrenceText": "recurrence_text",
    "triggerText": "trigger_text",
    "territoryScope": "territory_scope",
    "sourceClauseText": "source_clause_text",
    "responsibleParty": "responsible_party",
}

_PRODUCT_PRICE_CAMEL_TO_SNAKE: dict[str, str] = {
    "productName": "product_name",
    "productSku": "product_sku",
    "unitPrice": "unit_price",
    "listPriceReference": "list_price_reference",
    "productSpecificDiscountText": "product_specific_discount_text",
    "sourceClauseText": "source_clause_text",
}

_MOQ_CAMEL_TO_SNAKE: dict[str, str] = {
    "appliesPer": "applies_per",
    "productScope": "product_scope",
    "sourceClauseText": "source_clause_text",
}

_BUSINESS_RULE_CAMEL_TO_SNAKE: dict[str, str] = {
    "ruleCategory": "rule_category",
    "responsibleParty": "responsible_party",
    "sectionReference": "section_reference",
    "sourceClauseText": "source_clause_text",
}


def _merge_camel_into_snake(data: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
    out = dict(data)
    for camel, snake in aliases.items():
        if (out.get(snake) in (None, "")) and camel in out and out[camel] not in (None, ""):
            out[snake] = out[camel]
    return out


def _normalize_llm_extraction_keys(data: dict[str, Any]) -> dict[str, Any]:
    """LLMs often return camelCase; Pydantic `extra='ignore'` drops unknown keys → empty general fields."""
    out = _unnest_llm_extraction_dict(data)
    out = _merge_camel_into_snake(out, _EXTRACTION_CAMEL_TO_SNAKE)

    def _normalize_list(key: str, aliases: dict[str, str]) -> None:
        v = out.get(key)
        if isinstance(v, list):
            out[key] = [
                _merge_camel_into_snake(item, aliases) if isinstance(item, dict) else item
                for item in v
            ]

    _normalize_list("obligations", _OBLIGATION_CAMEL_TO_SNAKE)
    _normalize_list("product_price_list", _PRODUCT_PRICE_CAMEL_TO_SNAKE)
    _normalize_list("minimum_order_quantities", _MOQ_CAMEL_TO_SNAKE)
    _normalize_list("business_rules", _BUSINESS_RULE_CAMEL_TO_SNAKE)
    return out


def _call_llm_extraction(context_text: str) -> ContractExtractionDraft:
    provider = _get_llm_provider()
    default_model = "llama3.2:3b" if provider == "ollama" else "gpt-4.1"
    model = os.environ.get("CONTRACT_EXTRACT_MODEL", "").strip()
    if not model:
        model = os.environ.get("OLLAMA_MODEL", "").strip()
    if not model:
        model = os.environ.get("AI_MAPPING_MODEL", "").strip()
    if not model:
        model = default_model
    client = _get_openai_client()
    user_content = (
        _EXTRACTION_SYSTEM_RULES
        + "\n\n"
        + _extraction_json_schema_instruction()
        + "\n\nRECALLED CONTRACT EXCERPTS:\n"
        + context_text
    )

    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You output only valid JSON objects."},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        raw_s = completion.choices[0].message.content or "{}"
    except Exception as e:
        return ContractExtractionDraft(
            extraction_notes=f"LLM extraction failed: {type(e).__name__}: {e}",
            confidence_score=0.0,
        )

    try:
        raw_dict = json.loads(raw_s)
    except json.JSONDecodeError as e:
        return ContractExtractionDraft(
            extraction_notes=f"LLM returned invalid JSON: {e}",
            confidence_score=0.0,
        )

    if not isinstance(raw_dict, dict):
        raw_dict = {}
    data = _normalize_llm_extraction_keys(raw_dict)

    fe = _parse_field_evidence(data)
    data.pop("field_evidence", None)
    data.pop("fieldEvidence", None)
    draft = ContractExtractionDraft.model_validate(data)
    draft.field_evidence = fe
    return draft


def extract_contract_fields(
    doc: NormalizedDocument,
    *,
    use_llm: bool = False,
) -> ContractExtractionDraft:
    """
    If `use_llm=False`: return recall statistics only (no API call).
    If `use_llm=True`: build recall context → OpenAI JSON extraction → `ContractExtractionDraft`.
    """
    if not use_llm:
        recall_stats = {g: len(recall_chunks_for_field_group(doc, g)) for g in FIELD_GROUP_KEYWORDS}
        notes = (
            "No LLM. Keyword recall hit counts per group = "
            + repr(recall_stats)
            + ". Run chunking (`enrich_normalized_document`) first. Set use_llm=True to extract."
        )
        return ContractExtractionDraft(extraction_notes=notes, confidence_score=None)

    ctx = build_recall_context_for_llm(doc)
    if not ctx.strip():
        return ContractExtractionDraft(
            extraction_notes="No text in NormalizedDocument; run OCR + chunking first.",
            confidence_score=0.0,
        )
    try:
        draft = _call_llm_extraction(ctx)
        _enrich_field_evidence_spans(doc, draft)
        return draft
    except Exception as e:
        return ContractExtractionDraft(
            extraction_notes=f"LLM extraction failed: {type(e).__name__}: {e}",
            confidence_score=0.0,
        )
