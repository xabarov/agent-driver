"""Typed views over runtime metadata.

These helpers intentionally preserve the existing serialized
``AgentRunOutput.metadata`` shape. They give runtime code an owned place for
metadata reads/writes while the internal state model is migrated away from
ad hoc string keys.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any, Protocol

from agent_driver.context import (
    COMPACTION_AUDIT_KEY,
    COMPACTION_DECISION_KEY,
    COMPACTION_FAILURES_KEY,
    COMPACTION_RESULT_KEY,
)

JsonDict = dict[str, Any]
Metadata = MutableMapping[str, Any]


class HasRuntimeMetadata(Protocol):
    """Minimal protocol for runtime objects backed by metadata."""

    metadata: Metadata


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

    def terminal_output(self) -> JsonDict | None:
        return self.dict_or_none("terminal_output")

    @property
    def llm_step_count(self) -> int:
        return int(self.metadata.get("llm_step_count", 0))

    @llm_step_count.setter
    def llm_step_count(self, value: int) -> None:
        self.metadata["llm_step_count"] = value

    def workspace_cwd(self) -> str | None:
        value = self.metadata.get("workspace_cwd")
        return value if isinstance(value, str) and value.strip() else None

    def eval_sandbox_dir(self) -> str | None:
        value = self.metadata.get("eval_sandbox_dir")
        return value if isinstance(value, str) and value.strip() else None

    def interrupt_payload(self) -> JsonDict | None:
        return self.dict_or_none("interrupt_payload")

    def set_step_transition(self, *, next_step: str, tool_calls: int) -> None:
        self.metadata.update(
            {
                "next_step": next_step,
                "step_count": self.step_count,
                "tool_calls": tool_calls,
            }
        )

    def set_llm_step_transition(self, *, tool_calls: int) -> None:
        self.metadata.update(
            {
                "next_step": "tool_stage",
                "step_count": self.step_count,
                "llm_step_count": self.llm_step_count,
                "tool_calls": tool_calls,
            }
        )


class ToolLoopState(_MetadataView):
    """Tool-loop results, traces and repair controls."""

    def tool_results(self) -> list[JsonDict]:
        return self.list_of_dicts("tool_results")

    def tool_trace(self) -> list[JsonDict]:
        return self.list_of_dicts("tool_trace")

    def skill_invocations(self) -> list[JsonDict]:
        return self.list_of_dicts("skill_invocations")

    def append_skill_invocation(self, invocation: JsonDict) -> None:
        existing = self.metadata.get("skill_invocations")
        if not isinstance(existing, list):
            existing = []
        existing.append(invocation)
        self.metadata["skill_invocations"] = existing
        refs = self.metadata.get("invoked_skill_refs")
        if not isinstance(refs, list):
            refs = []
        refs.append(
            {
                "name": invocation.get("name"),
                "path": invocation.get("path"),
                "digest": invocation.get("digest"),
                "trusted": invocation.get("trusted"),
                "agent_id": invocation.get("agent_id"),
                "content_kind": invocation.get("content_kind"),
                "relative_file": invocation.get("relative_file"),
            }
        )
        self.metadata["invoked_skill_refs"] = refs

    def append_stage_outputs(
        self, *, traces: list[JsonDict], results: list[JsonDict]
    ) -> None:
        existing_trace = self.metadata.get("tool_trace")
        if not isinstance(existing_trace, list):
            existing_trace = []
        existing_results = self.metadata.get("tool_results")
        if not isinstance(existing_results, list):
            existing_results = []
        existing_trace.extend(traces)
        existing_results.extend(results)
        self.metadata["tool_trace"] = existing_trace
        self.metadata["tool_results"] = existing_results

    def force_final_answer(self, *, reason: str) -> None:
        self.metadata["force_final_answer"] = True
        self.metadata["tool_choice_override"] = "none"
        self.metadata["force_final_answer_reason"] = reason

    def ensure_force_final_answer(self, *, reason: str) -> None:
        self.metadata["force_final_answer"] = True
        self.metadata["tool_choice_override"] = "none"
        self.metadata.setdefault("force_final_answer_reason", reason)

    def clear_force_final_answer(self) -> None:
        self.metadata.pop("force_final_answer", None)
        self.metadata.pop("tool_choice_override", None)
        self.metadata.pop("force_final_answer_reason", None)

    def force_final_answer_enabled(self) -> bool:
        return self.metadata.get("force_final_answer") is True

    def force_final_answer_reason(self) -> str | None:
        value = self.metadata.get("force_final_answer_reason")
        return value if isinstance(value, str) and value else None

    def set_tool_choice_override(self, value: object) -> None:
        self.metadata["tool_choice_override"] = value

    def tool_choice_override(self) -> object | None:
        return self.metadata.get("tool_choice_override")

    def effective_tool_names(self) -> tuple[str, ...] | None:
        value = self.metadata.get("effective_tool_names")
        if isinstance(value, tuple):
            return tuple(str(item) for item in value)
        if isinstance(value, list):
            return tuple(str(item) for item in value)
        if isinstance(value, set):
            return tuple(str(item) for item in value)
        return None

    def pop_approved_tool_call(self) -> JsonDict | None:
        value = self.metadata.pop("approved_tool_call", None)
        return value if isinstance(value, dict) else None

    @property
    def tool_calls(self) -> int:
        return int(self.metadata.get("tool_calls", 0))

    @tool_calls.setter
    def tool_calls(self, value: int) -> None:
        self.metadata["tool_calls"] = value


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

    def planning_step(self) -> JsonDict | None:
        return self.dict_or_none("planning_step")

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

    def approved_plan(self) -> JsonDict | None:
        return self.dict_or_none("approved_plan")

    def clarification(self) -> str | None:
        value = self.metadata.get("clarification")
        return value if isinstance(value, str) else None

    def increment_tool_loops_since_todo_write(self) -> None:
        loops = int(self.metadata.get("tool_loops_since_todo_write", 0))
        self.metadata["tool_loops_since_todo_write"] = loops + 1

    def reset_todo_write_loop_counters(self, *, in_progress_id: str | None) -> None:
        self.metadata["tool_loops_since_todo_write"] = 0
        for key in list(self.metadata):
            if isinstance(key, str) and key.startswith("todo_hint_count_"):
                self.metadata.pop(key, None)
        if in_progress_id:
            self.metadata["last_in_progress_id"] = in_progress_id

    def todo_reminder_tool_loops(self, default: int) -> int:
        return int(self.metadata.get("todo_reminder_tool_loops", default))

    def tool_loops_since_todo_write(self) -> int:
        return int(self.metadata.get("tool_loops_since_todo_write", 0))

    def todo_hint_count(self, todo_id: str) -> int:
        return int(self.metadata.get(f"todo_hint_count_{todo_id}", 0))

    def increment_todo_hint_count(self, todo_id: str) -> None:
        key = f"todo_hint_count_{todo_id}"
        self.metadata[key] = int(self.metadata.get(key, 0)) + 1


class ResearchRuntimeState(_MetadataView):
    """Research evidence and final-readiness metadata."""

    def set_contract(
        self, *, payload: JsonDict, status: str, reasons: list[str]
    ) -> None:
        self.metadata["research_session_contract"] = payload
        self.metadata["final_readiness"] = status
        self.metadata["repair_required_reasons"] = reasons

    def set_contract_payload(self, payload: JsonDict) -> None:
        self.metadata["research_session_contract"] = payload

    def set_repair_exhausted(self, reasons: list[str]) -> None:
        self.metadata["final_readiness"] = "repair_exhausted"
        self.metadata["repair_required_reasons"] = reasons

    def contract_repair_nudge_count(self) -> int:
        return int(self.metadata.get("contract_repair_nudge_count", 0))

    def contract_repair_reason_signature(self) -> str | None:
        value = self.metadata.get("contract_repair_reason_signature")
        return value if isinstance(value, str) else None

    def set_contract_repair_reason_signature(self, signature: str) -> None:
        self.metadata["contract_repair_reason_signature"] = signature

    def fetch_fallback_required(self) -> bool:
        return self.metadata.get("research_fetch_fallback_required") is True

    def set_fetch_fallback_required(self) -> None:
        self.metadata["research_fetch_fallback_required"] = True

    def set_avoid_domains(self, domains: list[str]) -> None:
        self.metadata["research_avoid_domains"] = domains


class StreamingRuntimeState(_MetadataView):
    """Assistant streaming lifecycle metadata."""

    def started(self) -> bool:
        return self.metadata.get("assistant_stream_started") is True

    def completed(self) -> bool:
        return self.metadata.get("assistant_stream_completed") is True

    def content(self) -> str | None:
        value = self.metadata.get("assistant_stream_content")
        return value if isinstance(value, str) else None

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

    def set_raw_assistant_content(self, content: str) -> None:
        self.metadata["raw_assistant_content"] = content

    def raw_assistant_content(self) -> str | None:
        value = self.metadata.get("raw_assistant_content")
        return value if isinstance(value, str) else None


class CompactionRuntimeState(_MetadataView):
    """Context trimming, pressure and compaction diagnostics."""

    def token_pressure(self) -> JsonDict:
        return self.dict_or_empty("token_pressure")

    def token_pressure_state(self) -> str:
        return str(self.token_pressure().get("state", "ok"))

    def previous_token_pressure_state(self) -> str | None:
        value = self.metadata.get("previous_token_pressure_state")
        return value if isinstance(value, str) and value else None

    def set_previous_token_pressure_state(self, value: str) -> None:
        self.metadata["previous_token_pressure_state"] = value

    def set_trim_payload(self, payload: JsonDict) -> None:
        self.metadata["trim_audit"] = payload["trim_audit"]
        self.metadata["trim_metadata"] = payload["trim_metadata"]
        self.metadata["token_pressure"] = payload["token_pressure"]
        self.metadata["prompt_render"] = payload["prompt_render"]

    def observations(self) -> list[JsonDict]:
        return self.list_of_dicts("observations")

    def set_microcompaction(
        self,
        *,
        observations: list[JsonDict],
        audit: list[JsonDict],
        bytes_saved: int,
        estimated_tokens_saved: int,
    ) -> None:
        self.metadata["observations"] = observations
        self.metadata["microcompaction_audit"] = audit
        self.metadata["microcompaction"] = {
            "bytes_saved": bytes_saved,
            "estimated_tokens_saved": estimated_tokens_saved,
        }

    def digest_refs(self) -> list[JsonDict]:
        return self.list_of_dicts("digest_refs")

    def artifact_refs(self) -> list[JsonDict]:
        return self.list_of_dicts("artifact_refs")

    def invoked_skill_refs(self) -> list[JsonDict]:
        return self.list_of_dicts("invoked_skill_refs")

    def set_session_memory_extraction(self, payload: JsonDict) -> None:
        self.metadata["session_memory_extraction"] = payload

    def prompt_render(self) -> JsonDict | None:
        return self.dict_or_none("prompt_render")

    def output_metadata_projection(self) -> JsonDict:
        payload = {
            "observations": self.metadata.get("observations", []),
            "trim_audit": self.metadata.get("trim_audit", []),
            "trim_metadata": self.metadata.get("trim_metadata", {}),
            "microcompaction_audit": self.metadata.get("microcompaction_audit", []),
            "microcompaction": self.metadata.get("microcompaction", {}),
            "token_pressure": self.metadata.get("token_pressure", {}),
            COMPACTION_DECISION_KEY: self.metadata.get(COMPACTION_DECISION_KEY),
            COMPACTION_AUDIT_KEY: self.metadata.get(COMPACTION_AUDIT_KEY),
            COMPACTION_RESULT_KEY: self.metadata.get(COMPACTION_RESULT_KEY),
            COMPACTION_FAILURES_KEY: self.metadata.get(COMPACTION_FAILURES_KEY, []),
            "post_compact_cleanup": self.metadata.get("post_compact_cleanup", {}),
            "session_memory_extraction": self.metadata.get(
                "session_memory_extraction", {}
            ),
            "prompt_render": self.metadata.get("prompt_render"),
        }
        if "invoked_skill_refs" in self.metadata:
            payload["invoked_skill_refs"] = self.metadata.get("invoked_skill_refs", [])
        if "skill_invocations" in self.metadata:
            payload["skill_invocations"] = self.metadata.get("skill_invocations", [])
        return payload

    def memory_audit(self) -> JsonDict:
        payload = {
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
        if "invoked_skill_refs" in self.metadata:
            payload["invoked_skill_refs"] = self.metadata.get("invoked_skill_refs", [])
        return payload


class MemoryRuntimeState(_MetadataView):
    """Long-term memory recall block and one-time turn-sync guard."""

    def recalled_block(self) -> str | None:
        value = self.metadata.get("recalled_memory")
        return value if isinstance(value, str) and value.strip() else None

    def has_recalled(self) -> bool:
        return "recalled_memory" in self.metadata

    def set_recalled_block(self, block: str) -> None:
        self.metadata["recalled_memory"] = block

    def turn_synced(self) -> bool:
        return self.metadata.get("memory_synced") is True

    def mark_turn_synced(self) -> None:
        self.metadata["memory_synced"] = True


def get_loop_control_state(context: HasRuntimeMetadata) -> LoopControlState:
    return LoopControlState(context.metadata)


def get_tool_loop_state(context: HasRuntimeMetadata) -> ToolLoopState:
    return ToolLoopState(context.metadata)


def get_planning_runtime_state(context: HasRuntimeMetadata) -> PlanningRuntimeState:
    return PlanningRuntimeState(context.metadata)


def get_research_runtime_state(context: HasRuntimeMetadata) -> ResearchRuntimeState:
    return ResearchRuntimeState(context.metadata)


def get_streaming_runtime_state(context: HasRuntimeMetadata) -> StreamingRuntimeState:
    return StreamingRuntimeState(context.metadata)


def get_compaction_runtime_state(context: HasRuntimeMetadata) -> CompactionRuntimeState:
    return CompactionRuntimeState(context.metadata)


def get_memory_runtime_state(context: HasRuntimeMetadata) -> MemoryRuntimeState:
    return MemoryRuntimeState(context.metadata)


__all__ = [
    "CompactionRuntimeState",
    "HasRuntimeMetadata",
    "LoopControlState",
    "MemoryRuntimeState",
    "PlanningRuntimeState",
    "ResearchRuntimeState",
    "StreamingRuntimeState",
    "ToolLoopState",
    "get_compaction_runtime_state",
    "get_loop_control_state",
    "get_memory_runtime_state",
    "get_planning_runtime_state",
    "get_research_runtime_state",
    "get_streaming_runtime_state",
    "get_tool_loop_state",
]
