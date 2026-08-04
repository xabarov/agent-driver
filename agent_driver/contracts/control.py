"""Steering control-plane contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from typing import Any
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.enums.base import StrEnum
from agent_driver.contracts.validation import ensure_json_serializable


def utc_now_iso() -> str:
    """Return a stable UTC timestamp string for control records."""
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


class ControlKind(StrEnum):
    """Transport-neutral steering command kind."""

    INTERRUPT = "interrupt"
    ENQUEUE_USER_MESSAGE = "enqueue_user_message"
    CANCEL_QUEUED_MESSAGE = "cancel_queued_message"
    SET_MODEL = "set_model"
    SET_TOOL_POLICY = "set_tool_policy"
    SET_PERMISSION_MODE = "set_permission_mode"
    SET_MAX_THINKING_TOKENS = "set_max_thinking_tokens"
    PATCH_PLANNING_STATE = "patch_planning_state"
    STOP_SUBAGENT = "stop_subagent"
    CONTINUE_SUBAGENT = "continue_subagent"
    GET_CONTEXT_USAGE = "get_context_usage"
    # Epic 030 B: hard correction — abort the in-flight LLM request (not tools/
    # children), keep completed messages, add the text as a real user turn and
    # re-request. Degrades to ENQUEUE at a step boundary / during the tool phase.
    REDIRECT_USER_MESSAGE = "redirect_user_message"


class ControlPriority(StrEnum):
    """Command queue priority."""

    NOW = "now"
    NEXT = "next"
    LATER = "later"


class CommandQueueStatus(StrEnum):
    """Durable command queue lifecycle status."""

    QUEUED = "queued"
    APPLIED = "applied"
    CANCELLED = "cancelled"
    FAILED = "failed"


class LiveMessageSemantic(StrEnum):
    """Stable meaning of one live-message control."""

    STEER_CURRENT = "steer_current"
    REDIRECT_CURRENT = "redirect_current"
    QUEUE_NEXT = "queue_next"
    CANCEL_NEXT = "cancel_next"
    STOP = "stop"


class LiveMessagePhase(StrEnum):
    """Durable run phase used to resolve live-message consumption."""

    UNKNOWN = "unknown"
    LLM_IN_FLIGHT = "llm_in_flight"
    TOOL_IN_FLIGHT = "tool_in_flight"
    APPROVAL_PAUSE = "approval_pause"
    FINALIZING = "finalizing"
    TERMINAL = "terminal"


class LiveMessageAdmissionError(RuntimeError):
    """A live-message request was rejected before durable acceptance."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class LiveMessageIdempotencyError(RuntimeError):
    """One idempotency key was reused for non-verbatim request content."""

    reason_code = "idempotency_conflict"

    def __init__(self) -> None:
        super().__init__(self.reason_code)


class ControlRequest(ContractModel):
    """Host request to steer a live or resumable run."""

    kind: ControlKind
    run_id: str | None = None
    thread_id: str | None = None
    agent_id: str | None = None
    priority: ControlPriority = ControlPriority.NEXT
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str = "host"
    dedupe_key: str | None = None
    control_id: str = Field(default_factory=lambda: f"ctrl_{uuid4().hex[:12]}")
    created_at: str = Field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("payload", "metadata")
    @classmethod
    def validate_json_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure control payloads remain JSON-compatible."""
        return ensure_json_serializable(value, field_name="control payload")

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        """Normalize source label."""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("source must be non-empty")
        return cleaned

    @model_validator(mode="after")
    def validate_routing(self) -> "ControlRequest":
        """Require at least one stable routing identifier."""
        if not (self.run_id or self.thread_id or self.agent_id):
            raise ValueError("control request requires run_id, thread_id, or agent_id")
        return self


class ControlResponse(ContractModel):
    """Result of accepting or applying a control request."""

    ok: bool
    control_id: str | None = None
    queue_id: str | None = None
    error: str | None = None
    pending_approvals: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("pending_approvals", "metadata")
    @classmethod
    def validate_response_payload(cls, value: Any) -> Any:
        """Ensure response payload fields are JSON-compatible."""
        return ensure_json_serializable(value, field_name="control response payload")


class CommandQueueItem(ContractModel):
    """Durable queued steering command."""

    queue_id: str
    control_id: str
    schema_version: int = 0
    sequence: int = 0
    kind: ControlKind
    priority: ControlPriority
    status: CommandQueueStatus = CommandQueueStatus.QUEUED
    run_id: str | None = None
    thread_id: str | None = None
    agent_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str = "host"
    dedupe_key: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    accepted_at: str | None = None
    updated_at: str = Field(default_factory=utc_now_iso)
    applied_at: str | None = None
    cancelled_at: str | None = None
    failed_at: str | None = None
    terminal_at: str | None = None
    error: str | None = None
    requested_semantic: LiveMessageSemantic | None = None
    resolved_semantic: LiveMessageSemantic | None = None
    accepted_phase: LiveMessagePhase | None = None
    applied_phase: LiveMessagePhase | None = None
    applies_at: str | None = None
    reason_code: str | None = None
    content_sha256: str | None = None
    request_sha256: str | None = None
    source_generation: int = 0
    llm_generation: int = 0
    superseded_generation: int | None = None
    handoff_id: str | None = None
    destination_turn_id: str | None = None
    claimed_by: str | None = None
    claimed_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_request(cls, request: ControlRequest) -> "CommandQueueItem":
        """Create a queued item from a host control request."""
        now = utc_now_iso()
        message = request.payload.get("message") or request.payload.get("text")
        semantic = requested_semantic_for_request(request)
        return cls(
            queue_id=f"cmd_{uuid4().hex[:12]}",
            control_id=request.control_id,
            schema_version=1,
            kind=request.kind,
            priority=request.priority,
            run_id=request.run_id,
            thread_id=request.thread_id,
            agent_id=request.agent_id,
            payload=dict(request.payload),
            source=request.source,
            dedupe_key=request.dedupe_key,
            created_at=now,
            accepted_at=now,
            updated_at=now,
            requested_semantic=semantic,
            resolved_semantic=semantic,
            applies_at=applies_at_for_semantic(semantic),
            reason_code="accepted" if semantic is not None else None,
            content_sha256=(
                sha256(message.encode("utf-8")).hexdigest()
                if isinstance(message, str)
                else None
            ),
            request_sha256=control_request_sha256(request),
            metadata=dict(request.metadata),
        )

    @field_validator("payload", "metadata")
    @classmethod
    def validate_item_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure queued item payloads remain JSON-compatible."""
        return ensure_json_serializable(value, field_name="command queue payload")


class LiveRunState(ContractModel):
    """Durable generic phase/generation fence for one source run."""

    run_id: str
    thread_id: str | None = None
    agent_id: str | None = None
    phase: LiveMessagePhase = LiveMessagePhase.UNKNOWN
    llm_generation: int = 0
    stopped: bool = False
    updated_at: str = Field(default_factory=utc_now_iso)
    terminal_at: str | None = None


class NextTurnHandoff(ContractModel):
    """Stable host seam for idempotent creation of one subsequent turn."""

    handoff_id: str
    queue_id: str
    source_run_id: str
    source_thread_id: str | None = None
    message: str
    content_sha256: str
    sequence: int


class LiveMessageCapabilities(ContractModel):
    """Versioned public feature manifest for host fail-closed gates."""

    schema_id: str = Field(
        default="agent-driver.live-message-controls.v1",
        serialization_alias="schema",
    )
    soft_steer: bool = True
    hard_redirect: bool = True
    queue_next: bool = True
    cancel_queued: bool = True
    durable_store: str
    contract_version: int = 1


def requested_semantic_for_request(
    request: ControlRequest,
) -> LiveMessageSemantic | None:
    """Resolve an explicit semantic from transport-neutral kind and priority."""
    if request.kind is ControlKind.ENQUEUE_USER_MESSAGE:
        if request.priority is ControlPriority.NOW:
            return LiveMessageSemantic.STEER_CURRENT
        if request.priority is ControlPriority.NEXT:
            return LiveMessageSemantic.QUEUE_NEXT
    if (
        request.kind is ControlKind.REDIRECT_USER_MESSAGE
        and request.priority is ControlPriority.NOW
    ):
        return LiveMessageSemantic.REDIRECT_CURRENT
    if request.kind is ControlKind.CANCEL_QUEUED_MESSAGE:
        return LiveMessageSemantic.CANCEL_NEXT
    if request.kind is ControlKind.INTERRUPT:
        return LiveMessageSemantic.STOP
    return None


def applies_at_for_semantic(semantic: LiveMessageSemantic | None) -> str | None:
    """Return stable human/machine readback for the requested apply boundary."""
    if semantic is LiveMessageSemantic.STEER_CURRENT:
        return "next_safe_boundary"
    if semantic is LiveMessageSemantic.REDIRECT_CURRENT:
        return "in_flight_model_or_next_safe_boundary"
    if semantic is LiveMessageSemantic.QUEUE_NEXT:
        return "after_source_terminal"
    if semantic is LiveMessageSemantic.CANCEL_NEXT:
        return "before_next_handoff"
    if semantic is LiveMessageSemantic.STOP:
        return "run_abort_boundary"
    return None


def control_request_sha256(request: ControlRequest) -> str:
    """Hash the complete semantic request for verbatim idempotency checks."""
    payload = {
        "kind": request.kind.value,
        "run_id": request.run_id,
        "thread_id": request.thread_id,
        "agent_id": request.agent_id,
        "priority": request.priority.value,
        "payload": request.payload,
        "source": request.source,
        "metadata": request.metadata,
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


__all__ = [
    "CommandQueueItem",
    "CommandQueueStatus",
    "ControlKind",
    "ControlPriority",
    "ControlRequest",
    "ControlResponse",
    "LiveMessageAdmissionError",
    "LiveMessageCapabilities",
    "LiveMessageIdempotencyError",
    "LiveMessagePhase",
    "LiveMessageSemantic",
    "LiveRunState",
    "NextTurnHandoff",
    "applies_at_for_semantic",
    "control_request_sha256",
    "requested_semantic_for_request",
    "utc_now_iso",
]
