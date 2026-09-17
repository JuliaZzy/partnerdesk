"""CLI: read a business-card photo into contact fields via GLM-4V.

    {"imageDataUrl": "data:image/...;base64,..."} on stdin
      | python -m email_agent.tasks.read_card

Prints {"ok": true, "result": {companyName, contactName, email, phone, country}}
with the model's raw values (any field may be null or the string "null").
businessCardVision.ts normalizes those and shapes the final BusinessCardResult.
"""

from __future__ import annotations

import os

from contract_ai._env import load_python_dotenv

load_python_dotenv()

from .._io import emit_err, emit_ok, read_stdin_json  # noqa: E402
from ..llm_client import extra_params, extract_text, parse_json_loose, zai_config  # noqa: E402

PROMPT = """You are reading a photo of a business card to extract the cardholder's contact details.
Return ONLY a JSON object with exactly these keys (no prose, no markdown fence):
{
  "companyName": string | null,
  "contactName": string | null,
  "email": string | null,
  "phone": string | null,
  "country": string | null
}
Rules:
- contactName is the person's full name, not the company.
- companyName is the organization on the card.
- email: the primary email address exactly as printed.
- phone: the primary phone number, keep the country code / + prefix if shown.
- country: the country from the address if present, in English (e.g. "United States", "Singapore"). If no country is on the card, return null — do not infer from a phone code or language.
- If a field is not on the card or is illegible, return null for it. Never invent values."""


def read_card(image_data_url: str) -> object:
    """Vision model reads the card. GLM_VISION_MODEL overrides the default glm-4v-flash."""
    client, _ = zai_config()
    model = (os.environ.get("GLM_VISION_MODEL") or "").strip() or "glm-4v-flash"

    completion = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            }
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
        **extra_params("glm"),
    )

    raw = extract_text(completion)
    if not raw:
        raise ValueError("The card reader returned nothing.")
    return parse_json_loose(raw)


def main() -> int:
    try:
        payload = read_stdin_json()
    except Exception as err:  # noqa: BLE001
        emit_err(f"Invalid stdin JSON: {err}")
        return 1

    image = payload.get("imageDataUrl") if isinstance(payload, dict) else None
    if not image or not isinstance(image, str):
        emit_err("No imageDataUrl provided.")
        return 1

    try:
        result = read_card(image)
    except Exception as err:  # noqa: BLE001 — any failure must reach Node as JSON
        emit_err(f"{type(err).__name__}: {err}")
        return 1

    emit_ok(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
