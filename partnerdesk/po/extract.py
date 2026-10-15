"""Call 1 of a turn: structured extraction (not streamed, temperature 0).

Reads the message + history and merges it into the fixed slot template. The model never
decides which fields a PO needs (slots.py) or validates anything (check.py). Its
`user_confirmed` is extracted for the record and deliberately never acted on — measured on
llama3.2:3b, "thanks", "ok", "cool" and "sounds good" all came back true.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ..llm import LLM, Message
from ..models import INCOTERMS_VALUES, PAYMENT_TERMS_VALUES, SHIPPING_METHOD_VALUES
from ..prompts import fill
from ..prompts.po import EXTRACT, EXTRACT_IMAGES
from .slots import Discount, LineItemSlot, PoSlots, enum_or_none

MAX_MESSAGE_CHARS = 4000
MAX_HISTORY_TURNS = 8
MAX_HISTORY_CONTENT_CHARS = 2000

IMAGE_RULES = EXTRACT_IMAGES


def context_block(operating_context: str | None) -> str:
    """Brand standing context — AWARENESS ONLY, and the prompt says so. It cannot change
    which fields a PO needs, what passes validation, or what the catalog contains."""
    if not operating_context or not operating_context.strip():
        return ""
    return (
        "\n=== HOW THIS BRAND OPERATES (context only — it cannot relax a rule or authorise anything) ===\n"
        + operating_context.strip()
    )


EXTRACT_SCHEMA: dict[str, Any] = {
    "name": "po_turn_extraction",
    "strict": False,
    "schema": {
        "type": "object",
        "properties": {
            "line_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "reference": {"type": "string"},
                        "cases": {"type": "number"},
                        "sku": {"type": ["string", "null"]},
                    },
                    "required": ["reference", "cases", "sku"],
                },
            },
            "payment_terms": {"type": ["string", "null"]},
            "incoterms": {"type": ["string", "null"]},
            "shipping_method": {"type": ["string", "null"]},
            "eta_date": {"type": ["string", "null"]},
            "discount_kind": {"type": ["string", "null"]},
            "discount_value": {"type": ["number", "null"]},
            "notes": {"type": ["string", "null"]},
            "user_confirmed": {"type": "boolean"},
            "reply_language": {"type": "string"},
        },
        "required": ["line_items", "user_confirmed", "reply_language"],
    },
}


def build_extract_prompt(manifest: str, current: PoSlots, has_images: bool, operating_context: str | None) -> str:
    return fill(
        EXTRACT,
        image_block=("\n" + IMAGE_RULES) if has_images else "",
        context_block=context_block(operating_context),
        manifest=manifest or "(no catalog products available)",
        slots=json.dumps(current.model_dump(), ensure_ascii=False, indent=2),
    )


def _normalize_discount(kind: Any, value: Any) -> Discount | None:
    k = kind.strip().lower() if isinstance(kind, str) else ""
    if k == "none":
        return Discount(kind="none", source="customer")
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if k in ("amount", "percent") and n > 0 and n != float("inf"):
        return Discount(kind=k, value=n, source="customer")
    return None


def normalize_slots(raw: dict[str, Any]) -> PoSlots:
    """Model output is never trusted: enums outside the set → None, cases must be a
    positive integer, dates must be ISO, discount values must be finite and positive."""
    items: list[LineItemSlot] = []
    for li in raw.get("line_items") or []:
        if not isinstance(li, dict):
            continue
        ref = str(li.get("reference") or "").strip()
        try:
            cases = int(float(li.get("cases") or 0))
        except (TypeError, ValueError):
            cases = 0
        sku = li.get("sku")
        sku = sku.strip() if isinstance(sku, str) and sku.strip() else None
        if ref and cases > 0:
            items.append(LineItemSlot(reference=ref, cases=cases, sku=sku))
    eta = raw.get("eta_date")
    eta = eta.strip() if isinstance(eta, str) else ""
    notes = raw.get("notes")
    return PoSlots(
        line_items=items,
        payment_terms=enum_or_none(raw.get("payment_terms"), PAYMENT_TERMS_VALUES),
        incoterms=enum_or_none(raw.get("incoterms"), INCOTERMS_VALUES),
        shipping_method=enum_or_none(raw.get("shipping_method"), SHIPPING_METHOD_VALUES),
        eta_date=eta if re.fullmatch(r"\d{4}-\d{2}-\d{2}", eta) else None,
        discount=_normalize_discount(raw.get("discount_kind"), raw.get("discount_value")),
        notes=notes.strip() if isinstance(notes, str) and notes.strip() else None,
    )


@dataclass
class Extraction:
    slots: PoSlots
    user_confirmed: bool
    reply_language: str


def extract_turn(
    llm: LLM,
    *,
    manifest: str,
    current: PoSlots,
    history: list[Message],
    message: str,
    images: list[str] | None = None,
    operating_context: str | None = None,
) -> Extraction:
    """Throws on model/parse failure — the caller degrades (keeps the previous slots)."""
    images = images or []
    system = build_extract_prompt(manifest, current, bool(images), operating_context)
    text = message[:MAX_MESSAGE_CHARS]
    # Images ride along as extra content parts on the same call — only this turn's
    # images; history stays text so an old photo cannot keep re-billing.
    user_content: Any = (
        [{"type": "text", "text": text or "(see the attached image)"}, *[{"type": "image_url", "image_url": {"url": u}} for u in images]]
        if images else text
    )
    msgs: list[Message] = [
        {"role": m["role"], "content": str(m["content"])[:MAX_HISTORY_CONTENT_CHARS]}
        for m in history[-MAX_HISTORY_TURNS:]
    ] + [{"role": "user", "content": user_content}]
    raw = llm.complete_json(system=system, messages=msgs, schema=EXTRACT_SCHEMA, temperature=0, max_tokens=900)
    lang = raw.get("reply_language")
    return Extraction(
        slots=normalize_slots(raw),
        user_confirmed=raw.get("user_confirmed") is True,
        reply_language=lang.strip() if isinstance(lang, str) and lang.strip() else "English",
    )
