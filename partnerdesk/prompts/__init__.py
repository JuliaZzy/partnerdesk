"""Prompt strings for each agent, one module per agent. `fill` substitutes `{{name}}`."""

from __future__ import annotations


def fill(template: str, **parts: str) -> str:
    """Replace `{{name}}` slots. JSON examples in the template keep ordinary `{braces}`."""
    for key, value in parts.items():
        template = template.replace("{{" + key + "}}", value)
    return template
