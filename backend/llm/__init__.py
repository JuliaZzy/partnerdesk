"""Model client. Import from here.

`client` sends the request. `parse` reads a JSON answer. `types` holds a tool
call and one assistant turn. `fake` is the test double.
"""

from .client import LLM, OpenAICompatLLM
from .fake import FakeLLM
from .parse import parse_json_loose
from .types import AssistantTurn, Message, ToolCall

__all__ = [
    "LLM",
    "AssistantTurn",
    "FakeLLM",
    "Message",
    "OpenAICompatLLM",
    "ToolCall",
    "parse_json_loose",
]
