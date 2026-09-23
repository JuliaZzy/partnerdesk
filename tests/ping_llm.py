"""Live ping against the campus catalog. Needs LLM_API_KEY and campus net / VPN.

    .venv\\Scripts\\python tests/ping_llm.py
    .venv\\Scripts\\python tests/ping_llm.py --model qwen
"""

from __future__ import annotations

import argparse
import sys

from partnerdesk.config import chat_models
from partnerdesk.llm import OpenAICompatLLM


def ping(llm: OpenAICompatLLM) -> str:
    return "".join(
        llm.stream_text(
            system="You are a helpful assistant.",
            messages=[{"role": "user", "content": "Only reply with 'pong'."}],
            temperature=0,
            max_tokens=128,
        )
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="one call name, or omit to ping every catalog model")
    args = ap.parse_args()

    base = OpenAICompatLLM()
    print("base_url:", base.settings.base_url)
    print("key_set:", bool(base.settings.api_key) and base.settings.api_key != "ollama")

    ids = [args.model] if args.model else [m.id for m in chat_models()]
    failed = 0
    for model_id in ids:
        llm = base.with_model(model_id)
        print(f"\n=== {llm.settings.model} ===")
        try:
            text = ping(llm)
            print("reply:", repr(text))
        except Exception as e:
            failed += 1
            print(f"FAIL: {type(e).__name__}: {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
