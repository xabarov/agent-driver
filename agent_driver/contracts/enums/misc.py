"""Cross-cutting enums for chat, artifacts, and profile metadata."""

from __future__ import annotations

from agent_driver.contracts.enums.base import StrEnum


class ChatRole(StrEnum):
    """Role for normalized chat message payloads."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ArtifactKind(StrEnum):
    """Artifact category for offloaded data."""

    TOOL_RESULT = "tool_result"
    FILE = "file"
    DIFF = "diff"
    PLAN = "plan"
    SUBAGENT_OUTPUT = "subagent_output"
    MEMORY = "memory"
    OTHER = "other"


class SensitivityLevel(StrEnum):
    """Sensitivity label for payload redaction and handling."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    SECRET = "secret"
    UNKNOWN = "unknown"


class AgentProfile(StrEnum):
    """Model action profile selected for one run."""

    CHAT_ONLY = "chat_only"
    TOOL_CALLING = "tool_calling"
    REACT_TEXT = "react_text"
    CODE_AGENT = "code_agent"


__all__ = ["AgentProfile", "ArtifactKind", "ChatRole", "SensitivityLevel"]
