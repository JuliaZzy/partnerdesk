"""CLI: write a short reminder digest for a brand from classified inbound mail.

    <digest context JSON on stdin> | python -m email_agent.tasks.compose_digest

Prints {"ok": true, "result": {"summary": "..."}} on the last stdout line.

The counts are computed in Node (grouped from agent_inbound_email_topics); this module only
turns the structured facts into one short, plain-language reminder in the brand's language.
It never invents numbers — it restates what it is given and points at what needs attention,
purchase orders first.
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

DIGEST_SCHEMA = {
    "name": "inbound_reminder_digest",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
    },
}

SYSTEM_PROMPT = """You write a SHORT reminder for a brand about the distributor emails their agent just classified. The brand skims this to know what needs attention.

Rules:
- Restate only the numbers you are given. Never invent a count, a topic, or a request.
- Lead with purchase orders when there are any — they are what the brand acts on first.
- 2 to 4 short sentences. No greeting, no sign-off, no markdown, no bullet list.
- Write in the language named by `locale` (e.g. 'zh' = Chinese, 'en' = English).
- If there is nothing of note (no topics and no requests), say plainly that nothing needs attention.

Output only valid JSON matching the schema."""

# GLM/Ollama ignore json_schema response_format, so spell the shape out in the prompt.
GLM_JSON_SHAPE_INSTRUCTIONS = """
Respond with ONLY a single raw JSON object — no markdown, no code fences. Exactly:
{"summary": string}"""


def build_user_prompt(ctx: dict) -> str:
    topics = ctx.get("topics") or []  # [{label, count}], highest first
    requests = ctx.get("requests") or []
    lines = [
        f"Locale: {ctx.get('locale') or 'en'}",
        f"Brand: {ctx['brandName']}" if ctx.get("brandName") else None,
        f"Window: last {ctx.get('windowDays') or 7} days",
        f"Distributor senders in scope: {ctx.get('distributorEmails') or 0}",
        "",
        "Topic counts (label: number of emails):",
    ]
    if topics:
        lines += [f"- {t.get('label')}: {t.get('count')}" for t in topics]
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append("Actionable requests distributors made:")
    if requests:
        lines += [f"- {r}" for r in requests]
    else:
        lines.append("- (none)")
    return "\n".join(line for line in lines if line is not None)


def compose_digest(ctx: dict) -> dict:
    client, model, provider = email_agent_config("compose")
    # Only OpenAI honours the json_schema response_format; GLM and local Ollama need the shape
    # spelled out, or a small model returns prose (or an empty/renamed field) and the caller
    # sees an empty summary.
    system = SYSTEM_PROMPT if provider == "openai" else f"{SYSTEM_PROMPT}\n{GLM_JSON_SHAPE_INSTRUCTIONS}"

    # gpt-5 is a reasoning model: it only allows the default temperature (1), so temperature
    # is omitted here — mirrors tasks/compose.py.
    resp = client.with_options(max_retries=1).chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": build_user_prompt(ctx)},
        ],
        **response_format(provider, DIGEST_SCHEMA),
        **extra_params(provider),
    )
    parsed = parse_json_loose(extract_text(resp))
    if not isinstance(parsed, dict):
        raise ValueError("Model did not return a JSON object")
    summary = parsed.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("Model returned an empty summary")
    return {"summary": summary.strip()}


def main() -> int:
    try:
        ctx = read_stdin_json()
    except Exception as err:  # noqa: BLE001
        emit_err(f"Invalid stdin JSON: {err}")
        return 1
    if not isinstance(ctx, dict):
        emit_err("Expected a digest context object on stdin.")
        return 1

    try:
        result = compose_digest(ctx)
    except Exception as err:  # noqa: BLE001 — any failure must reach Node as JSON
        emit_err(f"{type(err).__name__}: {err}")
        return 1

    emit_ok(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
