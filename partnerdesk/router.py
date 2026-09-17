"""Intent router: one chat entry point, the agent works out what the distributor is doing.

The taxonomy is a short, platform-owned list (docs/porting/05-intents.md). It is passed
to the model as INPUT — the schema's enum is built from it — so the prompt never hardcodes
categories. Multi-label with per-topic confidence; an empty list is a real answer.

Routing is not labelling: six labels, three specialists, and an explicit abstain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .llm import LLM


@dataclass(frozen=True)
class Topic:
    key: str
    label: str
    description: str


TOPICS: tuple[Topic, ...] = (
    Topic("purchase_order", "Purchase order",
          "Anything to do with an order: asking for prices, a quote or a discount; asking whether stock is available or what the lead time is; placing an order or changing quantities on one; asking about order status or confirmation; invoices, payment status and remittance for an order; shipping status, tracking, delivery problems, customs and freight; and returns, damaged or defective goods, and quality complaints about goods received. The whole life of an order, from the first price question to a return, is this one topic."),
    Topic("product_catalog", "Product & catalog",
          "Requests for product specifications, catalog details, new-product information, or marketing assets and images. Informational only — the moment a price, a quantity or an order is involved it is Purchase order, not this."),
    Topic("sales_reporting", "Sales reporting",
          "Submission of sell-through or sales data, monthly reports, or questions about reporting requirements. This is the distributor reporting on what they SOLD; ordering more is Purchase order."),
    Topic("marketing_promotion", "Marketing & promotion",
          "Campaigns, livestreams, promotions, co-marketing, or requests for promotional assets."),
    Topic("contract_terms", "Contract & terms",
          "Distribution agreements, renewals, exclusivity, territory, or other contractual terms. Commercial terms attached to a specific order (its price, its payment terms) are Purchase order; this is the agreement that governs the relationship."),
    Topic("general_relationship", "General / relationship",
          "Introductions, check-ins, thanks, and general correspondence that does not fit another topic."),
)
TOPIC_KEYS = tuple(t.key for t in TOPICS)

# topic → specialist. Everything not listed abstains.
SPECIALIST_FOR: dict[str, str] = {
    "purchase_order": "po",
    "contract_terms": "contract",
    "sales_reporting": "report",
}
# Specialists that need a document to work on — without one the message is talk, not work.
NEEDS_ATTACHMENT = {"contract", "report"}


def classify_schema(topic_keys: tuple[str, ...] = TOPIC_KEYS) -> dict[str, Any]:
    return {
        "name": "message_topics", "strict": True,
        "schema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "topics": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {"topic": {"type": "string", "enum": list(topic_keys)}, "confidence": {"type": "number"}},
                    "required": ["topic", "confidence"],
                }},
                "requests": {"type": "array", "items": {"type": "string"}},
                "summary": {"type": "string"},
            },
            "required": ["topics", "requests", "summary"],
        },
    }


SYSTEM_PROMPT = """You label messages a distributor sent to a brand over chat. The sender is a real, onboarded distributor.

You are given a fixed menu of topics. Do TWO things:

1. topics — Assign EVERY topic from the menu that the message is genuinely about, each with your own 0-to-1 confidence that it applies. A message may match several topics (e.g. a discount request that also reports a damaged unit) or none at all. Only use keys from the menu. If nothing on the menu fits, return an empty list — do NOT force a label.

2. requests — Extract the distinct, actionable things the sender is asking the brand to do, each as one short imperative line. If the message asks for nothing actionable, return an empty list.

Also give a one-sentence summary (under 140 chars) a human reviewer can scan.

Rules:
- Judge only from the message, its attachments' names, and the recent conversation. Do NOT invent topics, requests, products, or quantities that are not there.
- A message that merely mentions something in passing is not necessarily about that topic — apply a topic only when the message is actually about it.
- Prefer leaving a topic off over adding it at low confidence. Empty is an acceptable and often correct answer.
- confidence is your own estimate that the topic applies, from 0 to 1.

Respond with ONLY a raw JSON object: {"topics": [{"topic": string, "confidence": number}], "requests": [string], "summary": string}"""


@dataclass
class Classification:
    topics: list[tuple[str, float]]
    requests: list[str] = field(default_factory=list)
    summary: str = ""


def classify(llm: LLM, *, message: str, attachments: list[str], history: list[dict[str, str]], topics: tuple[Topic, ...] = TOPICS) -> Classification:
    menu = "\n".join(f"- {t.key} — {t.label}: {t.description}" for t in topics)
    recent = "\n".join(f"{m['role']}: {str(m['content'])[:500]}" for m in history[-4:])
    user = (
        f"=== TOPIC MENU ===\n{menu}\n\n"
        f"=== RECENT CONVERSATION ===\n{recent or '(none)'}\n\n"
        f"=== ATTACHMENTS ===\n{', '.join(attachments) or '(none)'}\n\n"
        f"=== MESSAGE ===\n{message[:4000] or '(no text)'}"
    )
    raw = llm.complete_json(system=SYSTEM_PROMPT, messages=[{"role": "user", "content": user}], schema=classify_schema(tuple(t.key for t in topics)), temperature=0, max_tokens=400)
    out: list[tuple[str, float]] = []
    allowed = {t.key for t in topics}
    for t in raw.get("topics") or []:
        if isinstance(t, dict) and t.get("topic") in allowed:
            try:
                out.append((t["topic"], max(0.0, min(1.0, float(t.get("confidence", 0))))))
            except (TypeError, ValueError):
                continue
    reqs = [r for r in raw.get("requests") or [] if isinstance(r, str) and r.strip()]
    return Classification(topics=out, requests=reqs, summary=str(raw.get("summary") or ""))


@dataclass
class Route:
    specialist: str | None  # po | contract | report | None (abstain)
    reason: str
    classification: Classification | None = None


def route(
    llm: LLM, *, message: str, attachments: list[str], history: list[dict[str, str]],
    active_specialist: str | None, stage: str | None, min_po_confidence: float = 0.0,
) -> Route:
    """Decide who handles this message.

    1. An active PO conversation (gathering / confirm) keeps every message — "use the
       hydrating toner" classifies as product talk, but it is continuing the order.
    2. Otherwise classify. `purchase_order` alone has a confidence floor: a shaky order
       must not read as an order. contract / report only take a message that carries a
       document. Anything else abstains — a human, not a guess.
    """
    if active_specialist == "po" and stage in ("gathering", "confirm"):
        return Route("po", "continuing the open order")
    cls = classify(llm, message=message, attachments=attachments, history=history)
    for key, conf in sorted(cls.topics, key=lambda t: -t[1]):
        if key == "purchase_order" and min_po_confidence > 0 and conf < min_po_confidence:
            continue
        spec = SPECIALIST_FOR.get(key)
        if not spec:
            continue
        if spec in NEEDS_ATTACHMENT and not attachments:
            continue
        return Route(spec, f"{key} ({conf:.2f})", cls)
    return Route(None, "no specialist for this message", cls)
