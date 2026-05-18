"""Runtime, events, warnings, and serialization enums."""

from __future__ import annotations

from agent_driver.contracts.enums_base import StrEnum


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
    TOKEN_DELTA = "token_delta"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_COMPLETED = "tool_call_completed"
    GUARDRAIL_DECISION = "guardrail_decision"
    CHECKPOINT_SAVED = "checkpoint_saved"
    INTERRUPT_REQUESTED = "interrupt_requested"
    RUN_PAUSED = "run_paused"
    SUBAGENT_STARTED = "subagent_started"
    SUBAGENT_COMPLETED = "subagent_completed"
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
