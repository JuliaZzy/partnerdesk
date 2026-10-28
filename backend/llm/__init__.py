"""Model client. Import from here.

`client` sends the request. `parse` reads a JSON answer. `types` holds a tool
call and one assistant turn. `fake` is the test double.
"""

from .client import GLMClient, GLMOCRClient, QwenClient, DeepSeekClient, SJTUClient
from .fake import FakeLLM
from .parse import parse_json_loose
from .types import AssistantTurn, Message, ToolCall

__all__ = [
    "GLMClient",
    "GLMOCRClient",
    "QwenClient",
    "DeepSeekClient",
    "SJTUClient",
    "AssistantTurn",
    "FakeLLM",
    "Message",
    "ToolCall",
    "parse_json_loose",
]
