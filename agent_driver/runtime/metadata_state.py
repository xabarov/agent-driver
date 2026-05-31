"""Typed views over runtime metadata.

These helpers intentionally preserve the existing serialized
``AgentRunOutput.metadata`` shape. They give runtime code an owned place for
metadata reads/writes while the internal state model is migrated away from
ad hoc string keys.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from agent_driver.context import (
    COMPACTION_AUDIT_KEY,
    COMPACTION_DECISION_KEY,
    COMPACTION_FAILURES_KEY,
    COMPACTION_RESULT_KEY,
)


JsonDict = dict[str, Any]
Metadata = MutableMapping[str, Any]


class _MetadataView:
    """Base class for compatibility-preserving metadata views."""

    def __init__(self, metadata: Metadata) -> None:
        self.metadata = metadata

    def dict_or_none(self, key: str) -> JsonDict | None:
        value = self.metadata.get(key)
        return value if isinstance(value, dict) else None

    def dict_or_empty(self, key: str) -> JsonDict:
        return self.dict_or_none(key) or {}

    def list_of_dicts(self, key: str) -> list[JsonDict]:
        value = self.metadata.get(key, [])
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]


class LoopControlState(_MetadataView):
    """Loop routing and terminal state."""

    @property
    def next_step(self) -> str:
        return str(self.metadata.get("next_step", "run_started"))

    @next_step.setter
    def next_step(self, value: str) -> None:
        self.metadata["next_step"] = value

    @property
    def step_count(self) -> int:
        return int(self.metadata.get("step_count", 0))

    @step_count.setter
    def step_count(self, value: int) -> None:
        self.metadata["step_count"] = value

    def set_terminal_output(self, payload: JsonDict) -> None:
        self.metadata["terminal_output"] = payload


class ToolLoopState(_MetadataView):
    """Tool-loop results, traces and repair controls."""

    def tool_results(self) -> list[JsonDict]:
        return self.list_of_dicts("tool_results")

    def tool_trace(self) -> list[JsonDict]:
        return self.list_of_dicts("tool_trace")

    def force_final_answer(self, *, reason: str) -> None:
        self.metadata["force_final_answer"] = True
        self.metadata["tool_choice_override"] = "none"
        self.metadata["force_final_answer_reason"] = reason

    def set_tool_choice_override(self, value: object) -> None:
        self.metadata["tool_choice_override"] = value


class PlanningRuntimeState(_MetadataView):
    """Planning state, approval payloads and todo dedupe markers."""

    def pop_seed(self) -> JsonDict | None:
        value = self.metadata.pop("planning_state_seed", None)
        return value if isinstance(value, dict) else None

    def planning_state(self) -> JsonDict | None:
        return self.dict_or_none("planning_state")

    def set_planning_state(self, payload: JsonDict) -> None:
        self.metadata["planning_state"] = payload

    def set_planning_step(self, payload: JsonDict) -> None:
        self.metadata["planning_step"] = payload

    def clear_todo_deduped(self) -> None:
        self.metadata.pop("todo_write_deduped", None)

    def is_todo_deduped(self) -> bool:
        return self.metadata.get("todo_write_deduped") is True

    def mark_todo_deduped(self) -> None:
        self.metadata["todo_write_deduped"] = True

    def last_todo_write_signature(self) -> str | None:
        value = self.metadata.get("last_todo_write_signature")
        return value if isinstance(value, str) else None

    def set_last_todo_write_signature(self, signature: str) -> None:
        self.metadata["last_todo_write_signature"] = signature


class ResearchRuntimeState(_MetadataView):
    """Research evidence and final-readiness metadata."""

    def set_contract(
        self, *, payload: JsonDict, status: str, reasons: list[str]
    ) -> None:
        self.metadata["research_session_contract"] = payload
        self.metadata["final_readiness"] = status
        self.metadata["repair_required_reasons"] = reasons

    def set_fetch_fallback_required(self) -> None:
        self.metadata["research_fetch_fallback_required"] = True

    def set_avoid_domains(self, domains: list[str]) -> None:
        self.metadata["research_avoid_domains"] = domains


class StreamingRuntimeState(_MetadataView):
    """Assistant streaming lifecycle metadata."""

    def mark_started(self) -> None:
        self.metadata["assistant_stream_started"] = True
        self.metadata["assistant_stream_completed"] = False
        self.metadata["assistant_stream_content"] = ""

    def set_content(self, content: str) -> None:
        self.metadata["assistant_stream_content"] = content

    def mark_completed(self, content: str) -> None:
        self.metadata["assistant_stream_completed"] = True
        self.metadata["assistant_stream_content"] = content

    def mark_tombstoned(self) -> None:
        self.metadata["assistant_stream_tombstoned"] = True

    def mark_recovered(self, *, content: str, reason: str) -> None:
        self.metadata["assistant_stream_completed"] = True
        self.metadata["assistant_stream_recovered"] = True
        self.metadata["assistant_stream_recovery_reason"] = reason
        self.metadata["assistant_stream_content"] = content


class CompactionRuntimeState(_MetadataView):
    """Context trimming, pressure and compaction diagnostics."""

    def token_pressure(self) -> JsonDict:
        return self.dict_or_empty("token_pressure")

    def memory_audit(self) -> JsonDict:
        return {
            "trim_audit": self.metadata.get("trim_audit", []),
            "microcompaction_audit": self.metadata.get("microcompaction_audit", []),
            "token_pressure": self.metadata.get("token_pressure", {}),
            "compaction_decision": self.metadata.get(COMPACTION_DECISION_KEY),
            "compaction_audit": self.metadata.get(COMPACTION_AUDIT_KEY),
            "compaction_result": self.metadata.get(COMPACTION_RESULT_KEY),
            "compaction_failures": self.metadata.get(COMPACTION_FAILURES_KEY, []),
            "post_compact_cleanup": self.metadata.get("post_compact_cleanup", {}),
            "session_memory_extraction": self.metadata.get(
                "session_memory_extraction", {}
            ),
            "retained_digest_ids": self.metadata.get("retained_digest_ids", []),
            "retained_artifact_ids": self.metadata.get("retained_artifact_ids", []),
        }


__all__ = [
    "CompactionRuntimeState",
    "LoopControlState",
    "PlanningRuntimeState",
    "ResearchRuntimeState",
    "StreamingRuntimeState",
    "ToolLoopState",
]
