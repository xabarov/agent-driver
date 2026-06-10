"""Top-level runtime input/output contracts."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator, model_validator

from agent_driver.contracts.artifacts import ArtifactRef, RunWarning, TraceRef
from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.checkpoints import CheckpointRef
from agent_driver.contracts.enums import AgentProfile, RunStatus, TerminalReason
from agent_driver.contracts.events import RuntimeEvent
from agent_driver.contracts.interrupts import InterruptRequest, ResumeCommand
from agent_driver.contracts.memory import MemoryProjection
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.profiles import PromptRenderResult
from agent_driver.contracts.serialization import ExecutorSerializationPolicy
from agent_driver.contracts.subagents import SubagentGroup, SubagentRun
from agent_driver.contracts.tools import ToolPolicyInput, ToolTrace
from agent_driver.contracts.usage import UsageSummary
from agent_driver.contracts.validation import (
    ensure_json_serializable,
    ensure_positive_float,
    ensure_positive_int,
)


class AgentRunInput(ContractModel):
    """App-facing request to start or continue a run."""

    input: str | None = None
    messages: list[ChatMessage] = Field(default_factory=list)
    thread_id: str | None = None
    run_id: str | None = None
    resume: ResumeCommand | None = None
    agent_id: str
    graph_preset: str
    agent_profile: AgentProfile = AgentProfile.REACT_TEXT
    model_role: str = "default"
    stream: bool = False
    prompt_template_id: str | None = None
    prompt_template_version: int | None = None
    serialization_policy: ExecutorSerializationPolicy | None = None
    tool_policy: ToolPolicyInput = Field(default_factory=ToolPolicyInput)
    deadline_seconds: float | None = None
    max_steps: int | None = None
    max_tool_calls: int | None = None
    cost_budget_usd: float | None = None
    temperature: float | None = None
    """Sampling temperature passed through to ``LlmRequest.temperature`` for
    every model call in the run. ``None`` leaves the provider default. Mirrors
    the OpenAI ``temperature`` parameter."""
    max_tokens: int | None = None
    """Max completion tokens passed through to ``LlmRequest.max_tokens`` for
    each model call. ``None`` leaves the provider/runtime default (the runtime
    may still reduce it on provider credit errors). Mirrors OpenAI
    ``max_tokens``."""
    user_id: str | None = None
    tenant_id: str | None = None
    workspace_id: str | None = None
    app_metadata: dict[str, Any] = Field(default_factory=dict)
    response_format: dict[str, Any] | None = None
    """Provider-level structured output enforcement. Mirrors the
    OpenAI ``response_format`` parameter and is passed through to
    ``LlmRequest.response_format`` (which the OpenAI-compatible adapter
    already plumbs to the wire).

    Accepted shapes:

    - ``None`` (default) — provider behaviour unchanged; model returns
      free-form text.
    - ``{"type": "json_object"}`` — model MUST return a JSON object;
      schema not enforced. Cheap shape correctness for callers that
      validate against pydantic on their side.
    - ``{"type": "json_schema", "json_schema": {"name": "...", "schema":
      {...}, "strict": true}}`` — model output is constrained to the
      supplied JSON Schema. Requires provider support (OpenAI 4o+,
      most OpenRouter routes). Errors surface as provider 400 — the
      caller decides whether to retry.

    See ``docs/patterns/forcing-tool-calls.md`` for the related
    ``tool_choice`` pattern and ``docs/patterns/structured-output.md``
    for the structured-extraction story end-to-end.
    """
    tool_choice: str | dict[str, Any] | None = None
    """Provider-level forcing for the next LLM call (and onwards until the
    inner loop overrides it). Mirrors the OpenAI / Anthropic ``tool_choice``
    field so callers can guarantee a specific tool is invoked instead of
    relying on prompt nudges.

    Accepted shapes:

    - ``None`` — model decides (current default behaviour; unchanged).
    - ``"auto"`` — equivalent to ``None`` for backends that distinguish.
    - ``"required"`` — model MUST call one of the available tools; a
      text-only completion is rejected by the provider.
    - ``"none"`` — model MUST NOT call any tool; text-only required.
    - ``{"type": "tool", "name": "<tool_name>"}`` — model MUST call this
      specific tool. The provider returns a ``tool_use`` block with the
      named tool; text-only is impossible.

    The runtime's own inner-loop state (e.g. switching to ``"none"`` after
    the last tool call to force a final answer, see
    ``context.metadata["tool_choice_override"]``) wins when it is set —
    this caller-supplied value is the starting point and the fallback.

    See ``docs/runtime/tool_choice.md`` for the use-case rationale and
    interaction with code-agent vs ReAct profiles.
    """

    @field_validator("deadline_seconds")
    @classmethod
    def validate_deadline(cls, value: float | None) -> float | None:
        """Validate positive deadline seconds when provided."""
        return ensure_positive_float(value, field_name="deadline_seconds")

    @field_validator("max_steps", "max_tool_calls")
    @classmethod
    def validate_positive_optional_ints(cls, value: int | None) -> int | None:
        """Validate positive numeric run limits."""
        return ensure_positive_int(value, field_name="run limit")

    @field_validator("cost_budget_usd")
    @classmethod
    def validate_cost_budget(cls, value: float | None) -> float | None:
        """Validate a positive USD cost budget when provided."""
        return ensure_positive_float(value, field_name="cost_budget_usd")

    @field_validator("app_metadata")
    @classmethod
    def validate_app_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure metadata stays JSON-compatible for transport."""
        return ensure_json_serializable(value, field_name="app_metadata")

    @field_validator("tool_choice")
    @classmethod
    def validate_tool_choice(
        cls, value: str | dict[str, Any] | None
    ) -> str | dict[str, Any] | None:
        """Validate provider-neutral tool choice payload.

        Mirrors ``LlmRequest.validate_tool_choice`` so the same value shape
        is accepted at the public seam. The narrower set of meaningful
        strings (``auto`` / ``required`` / ``none``) is documented but not
        enforced here — providers reject unknown values themselves, and
        adding a hard whitelist here would block experimental backends.
        """
        if value is None:
            return value
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            return ensure_json_serializable(value, field_name="tool_choice payload")
        raise ValueError("tool_choice must be string, object, or null")

    @field_validator("response_format")
    @classmethod
    def validate_response_format(
        cls, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Validate provider-neutral response_format payload.

        We don't enforce the specific OpenAI / Anthropic shape here —
        providers return their own 400 on a malformed payload, and a
        strict whitelist would block experimental backends.
        """
        if value is None:
            return value
        if isinstance(value, dict):
            return ensure_json_serializable(value, field_name="response_format payload")
        raise ValueError("response_format must be a JSON object or null")

    @model_validator(mode="after")
    def validate_input_presence(self) -> "AgentRunInput":
        """Require user input, message list, or resume command."""
        has_input = bool((self.input or "").strip())
        has_messages = len(self.messages) > 0
        has_resume = self.resume is not None
        if not (has_input or has_messages or has_resume):
            raise ValueError("one of input/messages/resume must be provided")
        return self


class ContextDiagnostics(ContractModel):
    """SDK-visible context pressure diagnostics for one output."""

    pressure: str = "ok"
    recommendation: str = "continue"
    token_pressure: dict[str, Any] = Field(default_factory=dict)

    @field_validator("token_pressure")
    @classmethod
    def validate_token_pressure(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure context diagnostics remain JSON-compatible."""
        return ensure_json_serializable(value, field_name="context diagnostics")


class AgentRunOutput(ContractModel):
    """Normalized output envelope for sync and streamed runs."""

    run_id: str
    attempt_id: str
    thread_id: str | None = None
    status: RunStatus
    answer: str | None = None
    messages: list[ChatMessage] = Field(default_factory=list)
    events: list[RuntimeEvent] = Field(default_factory=list)
    tool_trace: list[ToolTrace] = Field(default_factory=list)
    subagent_runs: list[SubagentRun] = Field(default_factory=list)
    subagent_groups: list[SubagentGroup] = Field(default_factory=list)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    memory_projection: MemoryProjection | None = None
    prompt_render: PromptRenderResult | None = None
    usage: UsageSummary | None = None
    warnings: list[RunWarning] = Field(default_factory=list)
    trace: TraceRef | None = None
    checkpoint: CheckpointRef | None = None
    interrupt: InterruptRequest | None = None
    memory_audit: dict[str, Any] | None = None
    terminal_reason: TerminalReason | None = None
    context: ContextDiagnostics = Field(default_factory=ContextDiagnostics)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("memory_audit")
    @classmethod
    def validate_memory_audit(
        cls, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Ensure optional memory audit is JSON-compatible."""
        if value is None:
            return value
        return ensure_json_serializable(value, field_name="memory_audit")

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure metadata stays JSON-compatible for transport."""
        return ensure_json_serializable(value, field_name="metadata")

    @model_validator(mode="after")
    def validate_status_invariants(self) -> "AgentRunOutput":
        """Enforce pause/terminal invariants and terminal event presence."""
        terminal_statuses = {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.TIMED_OUT,
        }
        if self.status == RunStatus.PAUSED and self.interrupt is None:
            raise ValueError("paused outputs require interrupt")
        if self.status in terminal_statuses and self.terminal_reason is None:
            raise ValueError("terminal outputs require terminal_reason")
        if self.status in terminal_statuses:
            event_types = {event.type.value for event in self.events}
            if not (
                "run_completed" in event_types
                or "run_failed" in event_types
                or "run_cancelled" in event_types
            ):
                raise ValueError("terminal outputs require terminal runtime event")
        return self
