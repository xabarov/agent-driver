"""Shared enum contracts for phase 0 models."""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """String enum base with stable JSON-friendly representation."""


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


class ToolRisk(StrEnum):
    """Risk level assigned to a tool invocation."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SideEffectClass(StrEnum):
    """Side-effect profile of a tool."""

    NONE = "none"
    READ_ONLY = "read_only"
    REVERSIBLE_WRITE = "reversible_write"
    IRREVERSIBLE_WRITE = "irreversible_write"
    EXTERNAL_ACTION = "external_action"


class ApprovalMode(StrEnum):
    """Policy mode describing when human approval is required."""

    NEVER = "never"
    ON_POLICY_MATCH = "on_policy_match"
    ALWAYS = "always"
    STEP_BY_STEP = "step_by_step"


class InterruptReason(StrEnum):
    """Reason for pausing the run and requesting input."""

    APPROVAL_REQUIRED = "approval_required"
    CLARIFICATION_REQUIRED = "clarification_required"
    GUARDRAIL_REVIEW = "guardrail_review"
    TOOL_ARGS_REVIEW = "tool_args_review"
    STATE_REVIEW = "state_review"
    MANUAL_PAUSE = "manual_pause"


class ResumeAction(StrEnum):
    """Allowed resume actions for a pending interrupt."""

    APPROVE = "approve"
    REJECT = "reject"
    EDIT = "edit"
    CLARIFY = "clarify"
    PATCH_STATE = "patch_state"
    CANCEL = "cancel"


class SubagentTerminalState(StrEnum):
    """Terminal state of child/subagent execution."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    KILLED = "killed"
    TIMED_OUT = "timed_out"


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


class ChatRole(StrEnum):
    """Role for normalized chat message payloads."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolTraceStatus(StrEnum):
    """Status progression of a tool trace row."""

    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    DENIED = "denied"
    TIMED_OUT = "timed_out"


class ToolPolicyMode(StrEnum):
    """Per-run tool policy mode."""

    ALLOW_TOOLS = "allow_tools"
    NO_TOOLS = "no_tools"
    CLARIFY = "clarify"
    APPROVAL_REQUIRED = "approval_required"


class ToolPolicyDecision(StrEnum):
    """Structured policy decision for one planned tool call."""

    ALLOW = "allow"
    DENY = "deny"
    INTERRUPT = "interrupt"


class GuardrailDecision(StrEnum):
    """Decision emitted by guardrail pipeline hook."""

    ALLOW = "allow"
    SANITIZE = "sanitize"
    BLOCK = "block"


class SubagentExecutionMode(StrEnum):
    """Execution mode for child agents."""

    SYNC = "sync"
    BACKGROUND = "background"


class SubagentStatus(StrEnum):
    """Normalized subagent task status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class ParentStateWriteMode(StrEnum):
    """How child output is merged into parent state."""

    BOUNDED_APPEND_ONLY = "bounded_append_only"
    REPLACE = "replace"
    NONE = "none"


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


class SerializationMode(StrEnum):
    """Safety mode for executor boundary serialization."""

    JSON_SAFE = "json_safe"
    UNSAFE_PICKLE_OPT_IN = "unsafe_pickle_opt_in"


class SubagentJoinPolicy(StrEnum):
    """Join policy for fan-out child run groups."""

    WAIT_ALL = "wait_all"
    WAIT_ANY = "wait_any"
    K_OF_N = "k_of_n"
    BEST_EFFORT_UNTIL_DEADLINE = "best_effort_until_deadline"
    RACE = "race"
    MANUAL_REVIEW = "manual_review"


class SubagentMergeMode(StrEnum):
    """Merge mode for child outputs in one group."""

    APPEND = "append"
    RANK = "rank"
    SYNTHESIZE = "synthesize"
    VOTE = "vote"
    MANUAL = "manual"


class SubagentGroupStatus(StrEnum):
    """Lifecycle status of one subagent fan-out group."""

    PENDING = "pending"
    RUNNING = "running"
    JOINED = "joined"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
