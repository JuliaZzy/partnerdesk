"""CLI: did this inbound email say the distributor has paid a prepaid PO?

    <context JSON on stdin> | python -m email_agent.tasks.classify_po_payment

Prints {"ok": true, "result": {"paid": bool, "confidence": number}} on the last stdout line.

This is a yes/no on top of an already-known order waiting for a payment
(prepayment, 50/50 remainder, or Net 30/60 after receipt). The caller only runs
it when there is exactly one such order to attach the mail to. It does not
record amounts, verify a bank, or draft a reply — Node flips the order to
payment_made so the brand can check it.
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

SCHEMA = {
    "name": "po_payment_claim",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "paid": {"type": "boolean"},
            "confidence": {"type": "number"},
        },
        "required": ["paid", "confidence"],
    },
}

SYSTEM_PROMPT = """You read one email from a distributor to a brand. The brand is waiting for them to pay — a prepayment, the remaining 50%, or a Net 30/60 balance after receipt.

Decide whether this email is the distributor saying they HAVE PAID (wire sent, remittance, transfer done, attached a receipt, "paid", "transferred", "sent the money").

paid=true ONLY when they claim the payment is already sent. Not when they:
- ask for bank details, an invoice, or the amount
- promise to pay later
- negotiate the amount or terms
- talk about a different order with no claim of payment
- send an unrelated message

confidence is your own 0-to-1 estimate that this reading is correct.

Output only valid JSON matching the schema. No markdown, no commentary."""

SHAPE_HINT = (
    "\nRespond with ONLY a single raw JSON object — no markdown, no code fences. "
    'Exactly {"paid": boolean, "confidence": number from 0 to 1}.'
)


def build_user_prompt(ctx: dict) -> str:
    lines = [
        f"PO number: {ctx['poNumber']}" if ctx.get("poNumber") else None,
        f"Amount we asked them to pay now: {ctx['amountDue']}" if ctx.get("amountDue") else None,
        f"Payment terms: {ctx['paymentTerms']}" if ctx.get("paymentTerms") else None,
        f"Subject: {ctx['subject']}" if ctx.get("subject") else "Subject: (none)",
        f"\nEmail body:\n{ctx.get('bodyText') or '(empty)'}",
    ]
    return "\n".join(line for line in lines if line is not None)


def classify(ctx: dict) -> dict:
    client, model, provider = email_agent_config("triage")
    system = SYSTEM_PROMPT if provider == "openai" else f"{SYSTEM_PROMPT}\n{SHAPE_HINT}"
    resp = client.with_options(max_retries=1).chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": build_user_prompt(ctx)},
        ],
        **response_format(provider, SCHEMA),
        **extra_params(provider),
    )
    parsed = parse_json_loose(extract_text(resp))
    if not isinstance(parsed, dict):
        raise ValueError("Model did not return a JSON object")
    return {"paid": bool(parsed.get("paid")), "confidence": parsed.get("confidence")}


def main() -> int:
    try:
        ctx = read_stdin_json()
    except Exception as err:  # noqa: BLE001
        emit_err(f"Invalid stdin JSON: {err}")
        return 1
    if not isinstance(ctx, dict) or (not ctx.get("bodyText") and not ctx.get("subject")):
        emit_err("Expected an inbound email context with at least subject or bodyText.")
        return 1
    try:
        result = classify(ctx)
    except Exception as err:  # noqa: BLE001
        emit_err(f"{type(err).__name__}: {err}")
        return 1
    emit_ok(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
