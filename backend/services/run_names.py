"""Closed names for one harness iteration.
`agent_runs.status` is the run state machine. One loop iteration writes one
`agent_steps` row, and this module is the only place that chooses that row's
name. Agents do not invent labels.

A tool iteration is named after the registered tool. A model iteration is
always `model`. Which agent owns the run stays on `agent_runs.run_type`， 
and that column only holds `RunType`: a purchase, a contract, a report, a data
query, a brand answer, a code fix, a repair, a reflection, a report review,
a report revision, or one desk chat turn.
"""

from __future__ import annotations

import re
from enum import StrEnum

# `web_search`, and maybe later `po.lookup_sku`. Dots separate a domain from the tool. 
_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")

class RunType(StrEnum):
    PO = "po"
    CONTRACT = "contract"
    REPORT = "report"
    DATA = "data"
    CODE = "code"
    BRAND = "brand"
    FIX = "fix"
    REFLECT = "reflect"
    REVIEW = "review"
    REVISE = "revise"
    CHAT = "chat"

class StepType(StrEnum):
    MODEL = "model"
    TOOL = "tool"

class StepStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

class StepEvent(StrEnum):
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    STEP_FAILED = "step_failed"
    MODEL_STARTED = "model_started"
    MODEL_COMPLETED = "model_completed"
    MODEL_FAILED = "model_failed"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    TOOL_FAILED = "tool_failed"

MODEL_STEP_NAME = StepType.MODEL.value

# Keys inside `checkpoints.state_json`.
CONTEXT_KEY = "context"
COMPLETED_TOOL_CALLS_KEY = "completed_tool_calls"
LAST_KEY = "last"

# `last` before any model call, after the first user message is stored.
LAST_INPUT = "user"
# `last` after the final model text is stored. 
LAST_REPLY = "assistant:reply"

def step_name(step_type: StepType | str, tool_name: str | None) -> str:
    """The only way to choose `agent_steps.name`"""
    kind = step_type if isinstance(step_type, StepType) else StepType(step_type)
    if kind is StepType.MODEL:
        if tool_name is not None:
            raise ValueError("a model step has no tool name.")
        return MODEL_STEP_NAME
    if tool_name is None or tool_name == MODEL_STEP_NAME or _TOOL_NAME.fullmatch(tool_name) is None:
        raise ValueError(f"Invalid tool name: {tool_name}")
    return tool_name

def checkpoint_last_before_tool(tool_name: str) -> str:
    """Stored before tool runs."""
    return f"assistant:tool_use:{step_name(StepType.TOOL, tool_name)}"

def checkpoint_last_after_tool(tool_name: str) -> str:
    """Stored after the tool result is back in the context."""
    return f"tool_result:{step_name(StepType.TOOL, tool_name)}"