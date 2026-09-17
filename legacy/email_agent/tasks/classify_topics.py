"""CLI: multi-label topic classification for one inbound brand<->distributor email.

    <context JSON on stdin> | python -m email_agent.tasks.classify_topics

Prints {"ok": true, "result": {topics, requests, summary}} on the last stdout line.

Unlike classify_inbound (which routes to the single PO agent and picks ONE of po/other/
unsure), this tags an email with EVERY applicable topic from a caller-supplied menu, each
with its own confidence, and separately extracts the distinct actionable requests the email
makes. The topic menu is passed in on stdin — it is Mydian's curated list (shared/
agentTopics.ts), the single source of truth — so this module never hardcodes the taxonomy.

An email can carry several topics (a discount ask that also reports a damaged unit) or none
(small talk). Empty `topics` is a real answer, not a failure: the caller surfaces it as
"unsure / add a topic", never forces a label.
"""

from __future__ import annotations

import json

from contract_ai._env import load_python_dotenv

load_python_dotenv()

from .._io import emit_err, emit_ok, read_stdin_json  # noqa: E402
from ..llm_client import (  # noqa: E402
    email_agent_config,
    extra_params,
    extract_text,
    parse_json_loose,
    response_format,
)


def build_schema(topic_keys: list[str]) -> dict:
    """A strict schema whose topic enum is the caller's menu, built per call."""
    return {
        "name": "inbound_email_topics",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "topics": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "topic": {"type": "string", "enum": topic_keys},
                            "confidence": {"type": "number"},
                        },
                        "required": ["topic", "confidence"],
                    },
                },
                "requests": {"type": "array", "items": {"type": "string"}},
                "summary": {"type": "string"},
            },
            "required": ["topics", "requests", "summary"],
        },
    }


SYSTEM_PROMPT = """You label inbound emails a distributor sent to a brand. The sender is a real, onboarded distributor.

You are given a fixed menu of topics. Do TWO things:

1. topics — Assign EVERY topic from the menu that the email is genuinely about, each with your own 0-to-1 confidence that it applies. An email may match several topics (e.g. a discount request that also reports a damaged unit) or none at all. Only use keys from the menu. If nothing on the menu fits, return an empty list — do NOT force a label.

2. requests — Extract the distinct, actionable things the sender is asking the brand to do, each as one short imperative line (e.g. "Extend the trial by 14 days", "Send the updated price list"). If the email asks for nothing actionable, return an empty list.

Also give a one-sentence summary (under 140 chars) a human reviewer can scan.

Rules:
- Judge only from the email content and thread context. Do NOT invent topics, requests, products, or quantities that are not there.
- A message that merely mentions something in passing is not necessarily about that topic — apply a topic only when the email is actually about it.
- Prefer leaving a topic off over adding it at low confidence. Empty is an acceptable and often correct answer.
- confidence is your own estimate that the topic applies, from 0 to 1.

Output only valid JSON matching the schema. No markdown, no commentary."""


def build_topic_menu(topics: list[dict]) -> str:
    lines = []
    for t in topics:
        key = t.get("key")
        label = t.get("label") or key
        desc = t.get("description") or ""
        lines.append(f"- {key} ({label}): {desc}")
    return "\n".join(lines)


def build_user_prompt(ctx: dict) -> str:
    menu = build_topic_menu(ctx.get("topics") or [])
    lines = [
        "Topic menu (use only these keys):",
        menu,
        "",
        f"Distributor: {ctx['distributorName']}" if ctx.get("distributorName") else None,
        f"From: {ctx['senderEmail']}" if ctx.get("senderEmail") else None,
        f"Subject: {ctx['subject']}" if ctx.get("subject") else "Subject: (none)",
    ]
    thread = ctx.get("threadContext")
    if thread:
        lines.append(f"\nEarlier in this thread (oldest first):\n{thread}")
    # An order photographed rather than typed carries almost no body text. Saying the
    # pictures exist stops a near-empty body reading as small talk, which is how an
    # image-only order used to be filed as general correspondence and never reach the PO
    # agent at all. This is a fact about the message, not a hint about its meaning - the
    # model still decides the topic.
    image_count = ctx.get("imageCount") or 0
    if image_count:
        lines.append(
            f"\nThis email carries {image_count} image attachment(s), which are not shown "
            "to you. Distributors often send an order as a photo of a handwritten sheet or "
            "a screenshot instead of typing it out, so a short or empty body here does not "
            "mean the email is small talk. Judge it together with the subject and thread."
        )
    lines.append(f"\nEmail body:\n{ctx.get('bodyText') or '(empty)'}")
    return "\n".join(line for line in lines if line is not None)


def classify(ctx: dict) -> dict:
    """Return {topics, requests, summary} — the model's raw multi-label decision."""
    topic_keys = [t["key"] for t in (ctx.get("topics") or []) if t.get("key")]
    if not topic_keys:
        raise ValueError("No topic menu supplied on stdin (ctx.topics is empty).")

    schema = build_schema(topic_keys)
    client, model, provider = email_agent_config("triage")

    # GLM/Ollama ignore json_schema response_format, so spell the shape out in the prompt.
    # Applied to BOTH (anything not OpenAI): without it a local model mirrors the menu's
    # `key` field and emits {"key": ...} instead of {"topic": ...}, which the caller drops.
    shape_hint = (
        "\nRespond with ONLY a single raw JSON object — no markdown, no code fences. "
        "Exactly these fields, and use the field name \"topic\" (NOT \"key\"): "
        '{"topics": [{"topic": one of the menu keys, "confidence": number 0 to 1}], '
        '"requests": [string], "summary": string}'
    )
    system = SYSTEM_PROMPT if provider == "openai" else f"{SYSTEM_PROMPT}\n{shape_hint}"

    resp = client.with_options(max_retries=1).chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": build_user_prompt(ctx)},
        ],
        **response_format(provider, schema),
        **extra_params(provider),
    )
    parsed = parse_json_loose(extract_text(resp))
    if not isinstance(parsed, dict):
        raise ValueError("Model did not return a JSON object")
    # Normalize each topic to {"topic", "confidence"} — a local model may name the id field
    # "key" (mirroring the menu) or "topic". Accept either so the TS side never silently drops
    # a real match on a field-name quirk.
    norm_topics = []
    for t in parsed.get("topics") or []:
        if not isinstance(t, dict):
            continue
        key = t.get("topic") or t.get("key")
        if key:
            norm_topics.append({"topic": key, "confidence": t.get("confidence")})
    return {
        "topics": norm_topics,
        "requests": parsed.get("requests") or [],
        "summary": parsed.get("summary") or "",
    }


def main() -> int:
    try:
        ctx = read_stdin_json()
    except Exception as err:  # noqa: BLE001
        emit_err(f"Invalid stdin JSON: {err}")
        return 1
    if not isinstance(ctx, dict) or (not ctx.get("bodyText") and not ctx.get("subject")):
        emit_err("Expected an inbound email context object with at least subject or bodyText.")
        return 1

    try:
        result = classify(ctx)
    except Exception as err:  # noqa: BLE001 — any failure must reach Node as JSON
        emit_err(f"{type(err).__name__}: {err}")
        return 1

    emit_ok(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
