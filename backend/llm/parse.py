"""Turn a model string into JSON.

Some providers wrap the object in a ```json fence. Anything else is invalid
and raises `JSONDecodeError`. This module does not repair broken JSON.
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"^```(?:json)?\s*([\s\S]*?)\s*```$", re.IGNORECASE)


def parse_json_loose(text: str) -> Any:
    """Parse a model's JSON answer, tolerating a ```json fence some providers add."""
    trimmed = (text or "").strip()
    m = _FENCE.match(trimmed)
    return json.loads(m.group(1) if m else trimmed)
