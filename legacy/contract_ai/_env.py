"""Load the repo root `.env` for local development (does not override existing OS env vars).

ChatGPT / OpenAI / Zhipu keys and contract pipeline settings live in the root `.env` file.
One unified file serves both runtimes (Node and Python) locally.
"""

from __future__ import annotations

from pathlib import Path


def load_python_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    python_dir = Path(__file__).resolve().parent.parent
    env_path = python_dir.parent / ".env"
    if env_path.is_file():
        load_dotenv(env_path, override=False)
