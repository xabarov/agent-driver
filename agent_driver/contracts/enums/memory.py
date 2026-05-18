"""Memory projection enums used in runtime events."""

from __future__ import annotations

from agent_driver.contracts.enums.base import StrEnum


class MemoryStepKind(StrEnum):
    """Kind of one projected memory step."""

    TASK = "task"
    SYSTEM_PROMPT = "system_prompt"
    ACTION = "action"
    PLANNING = "planning"
    FINAL_ANSWER = "final_answer"


class MemoryProjectionView(StrEnum):
    """Projection mode for persisted memory/event views."""

    FULL = "full"
    SUCCINCT = "succinct"
    REPLAY = "replay"


__all__ = ["MemoryProjectionView", "MemoryStepKind"]
