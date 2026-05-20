"""Runtime and execution lifecycle enums."""

from __future__ import annotations

from agent_driver.contracts.enums.base import StrEnum


class RunStatus(StrEnum):
    """Top-level run lifecycle state."""

    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class TerminalReason(StrEnum):
    """Terminal reason codes for completed or aborted runs."""

    FINAL_ANSWER = "final_answer"
    CANCELLED_BY_USER = "cancelled_by_user"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    MAX_STEPS_EXCEEDED = "max_steps_exceeded"
    TOOL_POLICY_DENIED = "tool_policy_denied"
    GUARDRAIL_BLOCKED = "guardrail_blocked"
    APPROVAL_REJECTED = "approval_rejected"
    RUNTIME_ERROR = "runtime_error"
    MODEL_ERROR = "model_error"
    PROVIDER_PROTOCOL = "provider_protocol"
    CHECKPOINT_ERROR = "checkpoint_error"


class RuntimeEventType(StrEnum):
    """Structured runtime event kinds."""

    RUN_STARTED = "run_started"
    RUN_QUEUED = "run_queued"
    RUN_RESUMED = "run_resumed"
    NODE_STARTED = "node_started"
    NODE_COMPLETED = "node_completed"
    LLM_CALL_STARTED = "llm_call_started"
    LLM_CALL_COMPLETED = "llm_call_completed"
    LLM_REQUEST_REJECTED = "llm_request_rejected"
    TOKEN_DELTA = "token_delta"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_COMPLETED = "tool_call_completed"
    GUARDRAIL_DECISION = "guardrail_decision"
    CHECKPOINT_SAVED = "checkpoint_saved"
    INTERRUPT_REQUESTED = "interrupt_requested"
    RUN_PAUSED = "run_paused"
    SUBAGENT_STARTED = "subagent_started"
    SUBAGENT_COMPLETED = "subagent_completed"
    SUBAGENT_GROUP_STARTED = "subagent_group_started"
    SUBAGENT_SPAWNED = "subagent_spawned"
    SUBAGENT_GROUP_JOIN_WAITING = "subagent_group_join_waiting"
    SUBAGENT_GROUP_JOINED = "subagent_group_joined"
    SUBAGENT_GROUP_CANCELLED = "subagent_group_cancelled"
    SUBAGENT_MERGE_STARTED = "subagent_merge_started"
    SUBAGENT_MERGE_COMPLETED = "subagent_merge_completed"
    SUBAGENT_GROUP_FAILED = "subagent_group_failed"
    ARTIFACT_CREATED = "artifact_created"
    MEMORY_COMPACTED = "memory_compacted"
    WARNING = "warning"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RUN_CANCELLED = "run_cancelled"


class EventSeverity(StrEnum):
    """Severity attached to runtime events."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class WarningSeverity(StrEnum):
    """Severity attached to run warnings."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class WarningSource(StrEnum):
    """Origin of warning emitted by the runtime."""

    RUNTIME = "runtime"
    MODEL = "model"
    TOOL = "tool"
    GUARDRAIL = "guardrail"
    CHECKPOINT = "checkpoint"
    EVAL = "eval"


class SerializationMode(StrEnum):
    """Safety mode for executor boundary serialization."""

    JSON_SAFE = "json_safe"
    UNSAFE_PICKLE_OPT_IN = "unsafe_pickle_opt_in"


__all__ = [
    "EventSeverity",
    "RunStatus",
    "RuntimeEventType",
    "SerializationMode",
    "TerminalReason",
    "WarningSeverity",
    "WarningSource",
]
