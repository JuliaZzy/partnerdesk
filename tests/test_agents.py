"""Each prompts module exposes string constants; fill() substitutes slots without touching JSON braces."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

from partnerdesk.po.extract import build_extract_prompt
from partnerdesk.po.slots import PoSlots
from partnerdesk.prompts import fill
from partnerdesk.prompts import data_query, rag, report
from partnerdesk.prompts.orchestrator import PLAN
from partnerdesk.prompts.po import EXTRACT
from partnerdesk.prompts.session_open import OPEN

ROOT = Path(__file__).resolve().parents[1] / "partnerdesk"


def test_registry_modules_import_and_have_prompt_strings():
    registry = json.loads((ROOT / "prompts" / "registry.json").read_text(encoding="utf-8"))
    for agent in registry["agents"]:
        mod = importlib.import_module(agent["module"])
        strings = [v for k, v in vars(mod).items() if k.isupper() and isinstance(v, str)]
        assert strings, agent["id"]
    mcp = json.loads((ROOT / "mcp" / "servers.json").read_text(encoding="utf-8"))
    assert set(mcp["mcpServers"]) == {"socialcrawl"}
    ids = {a["id"] for a in registry["agents"]}
    assert {"po", "contract", "report", "brand", "data", "session_open"} <= ids


def test_fill_leaves_json_braces_and_fills_slots():
    text = fill(EXTRACT, image_block="", context_block="", manifest="FO-100", slots='{"line_items": []}')
    assert "FO-100" in text and "{{manifest}}" not in text
    assert '{"line_items"' in text


def test_build_extract_prompt_uses_the_po_constants(catalog):
    prompt = build_extract_prompt(catalog.manifest(), PoSlots(), has_images=True, operating_context="Ship on Friday.")
    assert "ATTACHED IMAGE" in prompt and "Ship on Friday" in prompt and "FO-100" in prompt
    assert PLAN.startswith("You are the orchestrator")
    assert OPEN.startswith("You open a new chat")
    assert "PLACEHOLDER" in data_query.REFLECT and "PLACEHOLDER" in rag.REFLECT and "PLACEHOLDER" in report.REFLECT
