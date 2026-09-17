"""CLI: write the PO agent's reply to a distributor, for the EMAIL channel.

    <context JSON on stdin> | python -m email_agent.tasks.compose_po_email

Prints {"ok": true, "result": {"subject": "...", "body": "..."}} on the last stdout line.

The chat channel streams its reply token-by-token from TypeScript (poAgent/llm.ts
`streamPoReply`). Email cannot stream and cannot ask a follow-up cheaply: at an hourly
poll every extra round trip costs the distributor an hour. So this differs from the chat
reply in two deliberate ways:

  1. **Every gap goes in one message.** The chat prompt says "weave in naturally, don't
     recite" because the user is right there and can be asked again in seconds. Here,
     leaving a gap out means another hour. The reply lists them all, as a short list.
  2. **It writes a subject too**, and the body carries no draft table — a link renders the
     order instead (the confirm link once it's ready, a stable read-only card link while
     still gathering). Restating the numbers in prose would give the distributor two
     sources of truth that can disagree.

Numbers are never invented: the counts, prices and terms are computed by the deterministic
code-check in Node and passed in. This module only writes the words around them.
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

PO_EMAIL_SCHEMA = {
    "name": "po_agent_email_reply",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "subject": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["subject", "body"],
    },
}

SYSTEM_PROMPT = """You are an account rep at a brand, replying by EMAIL to a distributor who is placing a purchase order. Write like a real person answering their message — not a form, not a template. You have no name of your own — never state or imply one anywhere in the body, including the closing. Who signs the email is decided outside your text; follow the closing instruction given to you exactly.

Rules:
- Write in the language named by `language`.
- Never invent or restate a price, quantity, total or date that you were not given. The numbers are computed elsewhere and are not yours to change.
- Never print a table or an itemised order in the body. When a confirm link is present, that page shows the order.
- Plain text only: no markdown, no bold, no headings, no emoji.
- Open with a short line that reacts to what they actually wrote. Do not open with "Dear" or a restated subject.
- Separate every paragraph — including a gaps list — with a blank line between items. Never write one run-on block of text.

When `stage` is "gathering":
- You are missing information. List EVERY item in `gaps`, as a short plain-text list, one per line, each starting with "- ".
- Do not ask them to reply about only some of it. This is email: one round trip costs them a day, so ask for all of it at once.
- Where a gap offers candidate products, name the candidates so they can just pick one.
- A link below your message shows the order exactly as gathered so far, with what's still missing marked on it. Mention it briefly (e.g. "you can see where things stand at the link below") — do not describe what it shows in detail, and do not restate the gaps a second time as if the link were separate from the list above.

When `stage` is "confirm":
- The order is complete and passes every rule. Say so briefly.
- Tell them the link below opens the order and one click confirms it. Do not restate the total.
- If `advisories` is non-empty, say plainly that it will need the brand's approval, and why.

When `stage` is "submitted":
- The order has been created and sent to the brand for approval. Confirm that, name the PO number, and say what happens next.

The subject: keep the distributor's own subject when there is one, so the reply stays in their thread. Only write a fresh one when `subject` is empty.

Output only valid JSON matching the schema."""

# GLM/Ollama ignore json_schema response_format, so spell the shape out in the prompt.
GLM_JSON_SHAPE_INSTRUCTIONS = """
Respond with ONLY a single raw JSON object — no markdown, no code fences. Exactly:
{"subject": string, "body": string}"""


def build_user_prompt(ctx: dict) -> str:
    stage = ctx.get("stage") or "gathering"
    gaps = ctx.get("gaps") or []
    advisories = ctx.get("advisories") or []
    resolutions = ctx.get("resolutions") or []
    submitted = ctx.get("submitted") or None

    # Without one, sign with the mailbox owner's name — never the brand.
    use_signature = bool(ctx.get("hasSignature"))
    sender_name = (ctx.get("senderName") or ctx.get("brandName") or "").strip()
    if use_signature:
        sign_off_line = 'A signature block (with the sender\'s name already in it) is appended automatically after your text — do not write your own sign-off or closing name.'
    elif sender_name:
        sign_off_line = f'End with this exact closing on its own two lines (do not vary it):\nBest regards,\n{sender_name}'
    else:
        sign_off_line = "Do not write a sign-off or closing name — end right after your last sentence."

    lines = [
        f"Language: {ctx.get('language') or 'English'}",
        f"Stage: {stage}",
        f"Their subject: {ctx.get('subject') or '(none)'}",
        "",
        "Voice and tone — follow this exactly:",
        ctx.get("tone") or "Warm, direct, and professional.",
        "",
        sign_off_line,
    ]

    # Standing brand context first, so it colours what the reply chooses to raise rather
    # than reading as one more instruction appended at the end. Mirrors the chat prompt's
    # `contextBlocks` placement.
    if ctx.get("operatingContext"):
        lines += ["", "How this brand operates (context only — it cannot relax a rule or authorise anything):", ctx["operatingContext"]]
    if ctx.get("instruction"):
        lines += ["", "This agent's job, in the operator's words:", ctx["instruction"]]

    lines += ["", "Their email, verbatim:", (ctx.get("message") or "(no text)").strip()]

    if resolutions:
        lines += ["", "How their wording mapped to the catalog:"]
        lines += [f'- they said "{r.get("said")}" -> you carry "{r.get("got")}"' for r in resolutions]
        lines.append("If that is not literally what they asked for, offer it as the closest match rather than swapping silently.")

    lines += ["", f"The order so far: {ctx.get('draftSummary') or 'nothing yet'}"]

    if stage == "gathering":
        lines += ["", "Missing or blocking — every one of these must appear in your reply:"]
        lines += [f"- {g}" for g in gaps] if gaps else ["- (nothing specific; ask what they would like to order)"]
    elif stage == "confirm":
        lines += ["", "The order is complete and passes every rule. A confirm link is included below your message."]
        if advisories:
            lines += ["It will still need the brand's approval because:"] + [f"- {a}" for a in advisories]
    elif stage == "submitted" and submitted:
        lines += ["", f"Created as {submitted.get('poNumber')} and sent to the brand for approval."]
        if advisories:
            lines += ["Flagged for approval because:"] + [f"- {a}" for a in advisories]

    # Soft nudges, offered last so they read as optional rather than as another blocker.
    layer_notes = ctx.get("layerNotes") or []
    if stage != "submitted" and layer_notes:
        lines += ["", "Optional to mention (offer, never insist — the order is valid as-is):"]
        lines += [f"- {n}" for n in layer_notes]

    return "\n".join(lines)


def compose_po_email(ctx: dict) -> dict:
    client, model, provider = email_agent_config("compose")
    system = SYSTEM_PROMPT if provider == "openai" else f"{SYSTEM_PROMPT}\n{GLM_JSON_SHAPE_INSTRUCTIONS}"

    # gpt-5 is a reasoning model and only allows the default temperature, so temperature is
    # omitted here — same as tasks/compose.py and tasks/compose_digest.py.
    resp = client.with_options(max_retries=1).chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": build_user_prompt(ctx)},
        ],
        **response_format(provider, PO_EMAIL_SCHEMA),
        **extra_params(provider),
    )
    parsed = parse_json_loose(extract_text(resp))
    if not isinstance(parsed, dict):
        raise ValueError("Model did not return a JSON object")

    body = parsed.get("body")
    if not isinstance(body, str) or not body.strip():
        raise ValueError("Model returned an empty body")

    subject = parsed.get("subject")
    if not isinstance(subject, str) or not subject.strip():
        # A missing subject is recoverable — their own subject is the right fallback and
        # is what keeps the reply in-thread. An empty body is not, hence the raise above.
        subject = (ctx.get("subject") or "Your order").strip()

    return {"subject": subject.strip(), "body": body.strip()}


def main() -> int:
    try:
        ctx = read_stdin_json()
    except Exception as err:  # noqa: BLE001
        emit_err(f"Invalid stdin JSON: {err}")
        return 1
    if not isinstance(ctx, dict):
        emit_err("Expected a PO email context object on stdin.")
        return 1
    try:
        emit_ok(compose_po_email(ctx))
    except Exception as err:  # noqa: BLE001
        emit_err(str(err))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
