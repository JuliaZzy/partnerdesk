"""CLI: is this inbound email the distributor accepting a shipment?

    <context JSON on stdin> | python -m email_agent.tasks.classify_po_receipt

Prints {"ok": true, "result": {"verdict": "none"|"received_ok"|"received_ok_paid",
"confidence": number}} on the last stdout line.

The caller only runs this when a shipped order is waiting to be received or is
in the inspection window. Issues / damage are NOT this task — those stay none
so a later claim handler can own them. Payment-only mail on an already-inspected
order is classify_po_payment, not this.
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

VERDICTS = ["none", "received_ok", "received_ok_paid", "issues"]

SCHEMA = {
    "name": "po_receipt_claim",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "verdict": {"type": "string", "enum": VERDICTS},
            "confidence": {"type": "number"},
        },
        "required": ["verdict", "confidence"],
    },
}

SYSTEM_PROMPT = """You read one email from a distributor to a brand. The brand has shipped a purchase order. The distributor does not click buttons in an app — this email is how they accept the goods.

Pick exactly one verdict:

- received_ok: they say the goods arrived AND inspection is fine (received, all good, no issues, quality OK). They do NOT claim they have already paid a remaining balance.
- received_ok_paid: same as received_ok, AND they claim a payment is already sent (wire, remittance, "paid the balance", "transferred the rest").
- issues: they report damage, shortage, wrong items, quality problems, or want to file a claim — even if the rest of the shipment arrived.
- none: anything else — not about this shipment, only asking when it will arrive, ONLY saying they paid (no receipt/OK), promising to inspect later, or too unclear.

confidence is your own 0-to-1 estimate.

Output only valid JSON matching the schema. No markdown, no commentary."""

SHAPE_HINT = (
    "\nRespond with ONLY a single raw JSON object — no markdown, no code fences. "
    f'Exactly {{"verdict": one of {json.dumps(VERDICTS)}, "confidence": number from 0 to 1}}.'
)


def build_user_prompt(ctx: dict) -> str:
    lines = [
        f"PO number: {ctx['poNumber']}" if ctx.get("poNumber") else None,
        f"Payment terms: {ctx['paymentTerms']}" if ctx.get("paymentTerms") else None,
        f"Amount still due if they pay now: {ctx['amountDue']}" if ctx.get("amountDue") else None,
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
    verdict = parsed.get("verdict")
    if verdict not in VERDICTS:
        verdict = "none"
    return {"verdict": verdict, "confidence": parsed.get("confidence")}


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
