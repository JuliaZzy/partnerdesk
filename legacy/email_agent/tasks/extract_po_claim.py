"""CLI: pull damage-claim lines out of one inbound email.

    <context JSON on stdin> | python -m email_agent.tasks.extract_po_claim

Prints {"ok": true, "result": {"lines": [...]}} on the last stdout line.

Node resolves each line against the PO's real items and decides whether every
required value is filled. This module only reads what they wrote — it does not
invent a SKU, a quantity, or a product that is not in the email or the PO list.
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

ISSUE_TYPES = ["short", "damaged", "needs_repair", "other"]
RESOLUTIONS = ["refund", "reship"]

SCHEMA = {
    "name": "po_claim_lines",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "lines": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "reference": {"type": "string"},
                        "quantityDamaged": {"type": ["integer", "null"]},
                        "issueType": {"type": ["string", "null"]},
                        "resolutionType": {"type": ["string", "null"]},
                        "description": {"type": ["string", "null"]},
                    },
                    "required": ["reference", "quantityDamaged", "issueType", "resolutionType", "description"],
                },
            },
        },
        "required": ["lines"],
    },
}

SYSTEM_PROMPT = """You extract damage-claim lines from a distributor email about a received shipment.

The PO's products are listed for you. Only mention a product that the email is actually about, using a reference the brand would recognise (SKU, product name, or the words they used).

For each line:
- reference: SKU or product name from the email, or empty if they did not name a product
- quantityDamaged: the number of units/cases they say are affected, or null if they did not say
- issueType: short (missing qty), damaged, needs_repair, other — or null if unclear
- resolutionType: refund or reship only if they asked for one; otherwise null
- description: one short phrase of what is wrong, or null

Do not invent a quantity or a product. An email that only says "some units were damaged" with no count and no product is one line with empty reference and null quantity.

Output only valid JSON matching the schema. No markdown, no commentary."""

SHAPE_HINT = (
    "\nRespond with ONLY a single raw JSON object — no markdown, no code fences. "
    'Exactly {"lines": [{"reference": string, "quantityDamaged": integer or null, '
    f'"issueType": one of {json.dumps(ISSUE_TYPES)} or null, '
    f'"resolutionType": one of {json.dumps(RESOLUTIONS)} or null, '
    '"description": string or null}]}.'
)


def build_user_prompt(ctx: dict) -> str:
    lines = [
        f"PO number: {ctx['poNumber']}" if ctx.get("poNumber") else None,
        "Products on this PO:",
        ctx.get("poItems") or "(none)",
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
    return {"lines": parsed.get("lines") or []}


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
