"""Internal types for SingleAgentRunner (step loop, deps, pending interrupt)."""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from time import monotonic
from typing import TYPE_CHECKING, Any

from agent_driver.code_agent.executor import CodeActionExecutor
from agent_driver.context.artifacts import ArtifactStore, ContextStore
from agent_driver.context.planning.artifacts import PlanArtifactStore
from agent_driver.context.sessions import SessionStore
from agent_driver.contracts.artifacts import RedactionInfo
from agent_driver.contracts.checkpoints import CheckpointRef
from agent_driver.contracts.enums import (
    EventSeverity,
    RunStatus,
    RuntimeEventType,
    TerminalReason,
)
from agent_driver.contracts.interrupts import InterruptRequest
from agent_driver.contracts.profiles import HarnessProfile
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.contracts.tools import ToolCall, ToolResultEnvelope
from agent_driver.llm.contracts import LlmResponse
from agent_driver.llm.providers import LlmProvider
from agent_driver.memory.provider import MemoryProvider
from agent_driver.runtime.abort import RunAbortHandle
from agent_driver.runtime.control.abort_store import AbortLifecycleStore
from agent_driver.runtime.control.approval_store import ApprovalConsumptionStore
from agent_driver.runtime.control.protocols import CommandQueueStore
from agent_driver.runtime.lifecycle_hooks import RunLifecycleHook
from agent_driver.runtime.metadata_state import (
    get_loop_control_state,
    get_tool_loop_state,
)
from agent_driver.runtime.single_agent.lifecycle.config_sections import (
    CapabilitySettings,
    CodeAgentSettings,
    CompactionSettings,
    PythonToolSettings,
    SubagentSettings,
    TrimmingSettings,
)
from agent_driver.runtime.storage import CheckpointStore, RuntimeEventLog
from agent_driver.runtime.tool_gate import ToolGate
from agent_driver.runtime.tools import ToolExecutor
from agent_driver.subagents.mailbox import SubagentMailboxStore
from agent_driver.subagents.store import SubagentStore
from agent_driver.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from agent_driver.contracts.execution_lease import LeaseOwnership
    from agent_driver.execution.lease import ExecutionLeaseManager
    from agent_driver.execution.protocol import ExecutionBackend

_TRIMMING_FIELDS = {item.name for item in fields(TrimmingSettings)}
_COMPACTION_FIELDS = {item.name for item in fields(CompactionSettings)}
_SUBAGENT_FIELDS = {item.name for item in fields(SubagentSettings)}
_CODE_AGENT_FIELDS = {item.name for item in fields(CodeAgentSettings)}
_PYTHON_TOOL_FIELDS = {item.name for item in fields(PythonToolSettings)}
_CAPABILITY_FIELDS = {item.name for item in fields(CapabilitySettings)}
_RUNNER_CONFIG_EXTRA_FIELDS = {
    "default_hard_max_seconds",
    "default_idle_timeout_seconds",
    "default_max_tool_calls",
    "fallback_providers",
    "finalize_hook_timeout",
    "stage_heartbeat_seconds",
}

# Defensive backstop on the agent step loop. A run whose model never emits a
# final answer (e.g. a tool that always fails, or a tool-calling spiral) would
# otherwise loop forever, because AgentRunInput.max_steps/max_tool_calls/
# deadline all default to None. This config-level default applies ONLY when the
# run's own max_steps is None, so any explicit per-run budget still wins. Set
# RunnerConfig(default_max_steps=None) to opt back into a fully unbounded loop.
# Chosen high enough to never truncate legitimate deep/agentic runs (reference
# runtimes cap at 50-90 iterations); this is a safety net, not a tight budget.
DEFAULT_MAX_STEPS_BACKSTOP = 80

# Same philosophy for tool calls: a safety net, not a tight budget. Before this
# existed, hosts that omitted per-run budgets silently inherited a hardcoded
# fallback of 1 in the force-final check — the agent was forced to finalize
# after its FIRST tool call (observations.md 2026-07-18, MeetScript chat_v2:
# cross-meeting questions finalized mid-task with empty/meta answers).
DEFAULT_MAX_TOOL_CALLS_BACKSTOP = 32


@dataclass(init=False)
class RunnerConfig:
    """Configuration for durable single-agent runtime runner.

    Intentionally **not** ``slots=True``: with a custom ``__init__`` the slotted
    variant required every field to be declared in two synchronized places
    (the slot annotation and the assignment), and a missed slot raised an
    ``AttributeError`` at construction. Without slots, adding a config field is
    a single assignment in ``__init__``. The annotations below remain as
    documentation and power dataclass ``repr``/``eq``.
    """

    graph_id: str
    cancellation_probe: Callable[[], bool] | None
    # Epic 030 B: opt-in hard-redirect probe. Polled DURING the in-flight LLM
    # await; returns the correction text when the host has a pending redirect for
    # this run (else None), which aborts just that request and re-asks with the
    # correction as a real user turn. ``None`` (default) leaves the loop unchanged.
    redirect_probe: Callable[[], str | None] | None
    fail_after_step: str | None
    tool_executor: ToolExecutor | None
    session_store: SessionStore | None
    artifact_store: ArtifactStore | None
    context_store: ContextStore | None
    observation_max_chars: int
    include_planning_prompt: bool
    default_max_steps: int | None
    default_max_tool_calls_per_step: int | None
    budget_grace_enabled: bool
    defer_primer: Callable[[Any], Any] | None
    subagent_store: SubagentStore | None
    subagent_mailbox_store: SubagentMailboxStore | None
    code_executor: CodeActionExecutor | None
    # EPIC-01: host-injected execution backend for the built-in bash/read/write
    # tools. None keeps the default local subprocess + local-disk behavior. The
    # model never selects it; governance stays above dispatch.
    execution_backend: "ExecutionBackend | None"
    # EPIC-03: when set, a lease-capable backend acquires a workspace lease with
    # this ownership at run start (reused across steps, released/detached on exit).
    # None keeps stateless backend use (EPIC-01/02). A host-supplied attach ref in
    # ``app_metadata["execution_lease_ref"]`` attaches instead of acquiring.
    execution_lease_ownership: "LeaseOwnership | None"
    tool_registry: ToolRegistry | None
    command_queue_store: CommandQueueStore | None
    approval_store: ApprovalConsumptionStore | None
    abort_store: AbortLifecycleStore | None
    plan_artifact_store: PlanArtifactStore | None
    replay_prior_result: bool
    memory_provider: MemoryProvider | None
    memory_consolidation_every_n_turns: int
    capabilities: CapabilitySettings
    lifecycle_hooks: tuple[RunLifecycleHook, ...]
    trimming: TrimmingSettings
    compaction: CompactionSettings
    subagents: SubagentSettings
    code_agent: CodeAgentSettings
    python_tool: PythonToolSettings

    def __init__(self, **kwargs: Any) -> None:
        trimming = kwargs.pop("trimming", None) or TrimmingSettings(
            **{key: kwargs.pop(key) for key in list(kwargs) if key in _TRIMMING_FIELDS}
        )
        compaction = kwargs.pop("compaction", None) or CompactionSettings(
            **{
                key: kwargs.pop(key)
                for key in list(kwargs)
                if key in _COMPACTION_FIELDS
            }
        )
        subagents = kwargs.pop("subagents", None) or SubagentSettings(
            **{key: kwargs.pop(key) for key in list(kwargs) if key in _SUBAGENT_FIELDS}
        )
        code_agent = kwargs.pop("code_agent", None) or CodeAgentSettings(
            **{
                key: kwargs.pop(key)
                for key in list(kwargs)
                if key in _CODE_AGENT_FIELDS
            }
        )
        python_tool = kwargs.pop("python_tool", None) or PythonToolSettings(
            **{
                key: kwargs.pop(key)
                for key in list(kwargs)
                if key in _PYTHON_TOOL_FIELDS
            }
        )
        capabilities = kwargs.pop("capabilities", None) or CapabilitySettings(
            **{
                key: kwargs.pop(key)
                for key in list(kwargs)
                if key in _CAPABILITY_FIELDS
            }
        )
        self.graph_id = kwargs.pop("graph_id", "single_agent_runtime")
        self.cancellation_probe = kwargs.pop("cancellation_probe", None)
        self.redirect_probe = kwargs.pop("redirect_probe", None)
        self.fail_after_step = kwargs.pop("fail_after_step", None)
        self.tool_executor = kwargs.pop("tool_executor", None)
        self.session_store = kwargs.pop("session_store", None)
        self.artifact_store = kwargs.pop("artifact_store", None)
        self.context_store = kwargs.pop("context_store", None)
        self.observation_max_chars = kwargs.pop("observation_max_chars", 400)
        self.include_planning_prompt = kwargs.pop("include_planning_prompt", False)
        self.default_max_steps = kwargs.pop(
            "default_max_steps", DEFAULT_MAX_STEPS_BACKSTOP
        )
        self.default_max_tool_calls = kwargs.pop(
            "default_max_tool_calls", DEFAULT_MAX_TOOL_CALLS_BACKSTOP
        )
        self.default_max_tool_calls_per_step = kwargs.pop(
            "default_max_tool_calls_per_step", None
        )
        if (
            self.default_max_tool_calls_per_step is not None
            and self.default_max_tool_calls_per_step <= 0
        ):
            raise ValueError("default_max_tool_calls_per_step must be > 0")
        # Epic 019 wall-clock safety nets (openclaude QueryGuard reference): hard cap on
        # the whole run and an idle cap on a single step's await (wedged tool/provider).
        # None opts out; per-run deadline_seconds is an explicit caller budget on top.
        self.default_hard_max_seconds = kwargs.pop("default_hard_max_seconds", 1800.0)
        # 600s, not openclaude's 300s: our idle cap bounds a WHOLE step, and one llm_step
        # may legitimately chain several provider retries (~100s timeout each) through the
        # forced-final recovery ladder before producing progress.
        self.default_idle_timeout_seconds = kwargs.pop(
            "default_idle_timeout_seconds", 600.0
        )
        # Epic 016: providers tried (in order) by the forced-final ladder when the primary
        # keeps returning empty finals. Hosts pass configured LlmProvider instances.
        self.fallback_providers = tuple(kwargs.pop("fallback_providers", ()) or ())
        # Epic 024: per-hook budget (seconds) for finalize-stage lifecycle hooks
        # (goal-gate graders etc.). On expiry the hook fails open — the answer is
        # accepted and lifecycle_hook_timed_out is emitted. None disables. 15s fits
        # a small-completion LLM grader plus one transient retry; unbudgeted finalize
        # awaits are exactly how the measured 22-139s post-final tails happened.
        self.finalize_hook_timeout = kwargs.pop("finalize_hook_timeout", 15.0)
        # Epic 025: liveness heartbeat for long awaited stages (provider completion,
        # tool stage). Emits an info WARNING signal_id=stage_wait_heartbeat with
        # elapsed_ms every N seconds while the stage is still running, so a host
        # status label never freezes on a stale caption. None/0 disables.
        self.stage_heartbeat_seconds = kwargs.pop("stage_heartbeat_seconds", 10.0)
        # When a soft budget (max_steps / max_tool_calls / cost) is exhausted,
        # grant one forced-final synthesis turn (tools disabled) so the run can
        # return a best-effort answer instead of a bare FAILED with an empty
        # answer. Set False to restore the hard-fail-on-budget behaviour.
        self.budget_grace_enabled = kwargs.pop("budget_grace_enabled", True)
        # Optional defer primer: a ``Callable[[DeferPrimerInput], Iterable[str]]``
        # that, before each LLM step, selects which currently-deferred tools to
        # surface into the schema list (see ``llm_step.defer_primer``). None
        # (default) keeps the pure ``tool_search``-only deferral behaviour.
        self.defer_primer = kwargs.pop("defer_primer", None)
        self.subagent_store = kwargs.pop("subagent_store", None)
        self.subagent_mailbox_store = kwargs.pop("subagent_mailbox_store", None)
        self.code_executor = kwargs.pop("code_executor", None)
        self.execution_backend = kwargs.pop("execution_backend", None)
        self.execution_lease_ownership = kwargs.pop("execution_lease_ownership", None)
        self.tool_registry = kwargs.pop("tool_registry", None)
        self.command_queue_store = kwargs.pop("command_queue_store", None)
        self.approval_store = kwargs.pop("approval_store", None)
        self.abort_store = kwargs.pop("abort_store", None)
        self.plan_artifact_store = kwargs.pop("plan_artifact_store", None)
        # F2 — when True, a duplicate approve of an already-consumed interrupt
        # replays the prior recorded terminal output verbatim instead of raising
        # a conflict (requires an approval_store). Default False = prior behaviour.
        self.replay_prior_result = bool(kwargs.pop("replay_prior_result", False))
        self.memory_provider = kwargs.pop("memory_provider", None)
        # Epic 031: cadence for background memory consolidation (0 = off). The
        # host supplies the durable turn ordinal via app_metadata["memory"]
        # ["turn_ordinal"]; the memory hook fires consolidation when it lands on
        # a multiple of this interval.
        self.memory_consolidation_every_n_turns = int(
            kwargs.pop("memory_consolidation_every_n_turns", 0) or 0
        )
        self.capabilities = capabilities
        self.lifecycle_hooks = tuple(kwargs.pop("lifecycle_hooks", ()) or ())
        self.trimming = trimming
        self.compaction = compaction
        self.subagents = subagents
        self.code_agent = code_agent
        self.python_tool = python_tool
        if kwargs:
            raise TypeError(f"Unexpected RunnerConfig arguments: {sorted(kwargs)}")

    def with_overrides(self, **overrides: Any) -> "RunnerConfig":
        """Return a shallow copy with top-level attribute overrides applied.

        Shallow by design: callers only reassign top-level attributes
        (``tool_registry``, ``tool_executor``, ``memory_provider``,
        ``command_queue_store``); nested settings objects are shared but never
        mutated. This avoids ``deepcopy``, which cannot copy stateful deps such
        as a memory provider's DB connection or lock.
        """
        clone = copy.copy(self)
        for key, value in overrides.items():
            setattr(clone, key, value)
        return clone

    @property
    def trim_max_chars(self) -> int:
        return self.trimming.trim_max_chars

    @property
    def trim_max_messages(self) -> int | None:
        return self.trimming.trim_max_messages

    @property
    def trim_max_observations(self) -> int | None:
        return self.trimming.trim_max_observations

    @property
    def trim_protect_recent_turns(self) -> int | None:
        return self.trimming.trim_protect_recent_turns

    @property
    def microcompact_preserve_recent(self) -> int:
        return self.trimming.microcompact_preserve_recent

    @property
    def microcompact_max_preview_chars(self) -> int:
        return self.trimming.microcompact_max_preview_chars

    @property
    def context_window_estimate(self) -> int:
        return self.trimming.context_window_estimate

    @property
    def token_warning_threshold(self) -> int:
        return self.trimming.token_warning_threshold

    @property
    def token_compact_threshold(self) -> int:
        return self.trimming.token_compact_threshold

    @property
    def token_blocking_threshold(self) -> int:
        return self.trimming.token_blocking_threshold

    @property
    def output_token_reserve(self) -> int:
        return self.trimming.output_token_reserve

    @property
    def enable_compaction(self) -> bool:
        return self.compaction.enable_compaction

    @property
    def enable_session_memory_compaction(self) -> bool:
        return self.compaction.enable_session_memory_compaction

    @property
    def enable_llm_compaction(self) -> bool:
        return self.compaction.enable_llm_compaction

    @property
    def enable_partial_compaction(self) -> bool:
        return self.compaction.enable_partial_compaction

    @property
    def enable_ptl_retry(self) -> bool:
        return self.compaction.enable_ptl_retry

    @property
    def compaction_failure_limit(self) -> int:
        return self.compaction.compaction_failure_limit

    @property
    def session_memory_stale_after_turns(self) -> int:
        return self.compaction.session_memory_stale_after_turns

    @property
    def compaction_model(self) -> str:
        return self.compaction.compaction_model

    @property
    def ptl_retry_max_chars(self) -> int:
        return self.compaction.ptl_retry_max_chars

    @property
    def post_compact_max_reinjected_artifact_refs(self) -> int:
        return self.compaction.post_compact_max_reinjected_artifact_refs

    @property
    def enable_tool_arg_truncation(self) -> bool:
        return self.compaction.enable_tool_arg_truncation

    @property
    def tool_arg_truncation_max_chars(self) -> int:
        return self.compaction.tool_arg_truncation_max_chars

    # Capability delegating accessors (grouped in CapabilitySettings; kept as
    # top-level properties so existing readers/callers are unchanged).
    @property
    def enable_prompt_cache(self) -> bool:
        return self.capabilities.enable_prompt_cache

    @property
    def harness_profiles(self) -> tuple[HarnessProfile, ...]:
        return self.capabilities.harness_profiles

    @property
    def auxiliary_provider(self) -> LlmProvider | None:
        return self.capabilities.auxiliary_provider

    @property
    def auxiliary_model(self) -> str | None:
        return self.capabilities.auxiliary_model

    @property
    def auxiliary_models(self) -> dict[str, str]:
        return self.capabilities.auxiliary_models

    def aux_model_for(self, task: str) -> str | None:
        """Per-task aux-model resolution (epic 032 phase A)."""
        return self.capabilities.aux_model_for(task)

    @property
    def project_memory_sources(self) -> tuple[str, ...]:
        return self.capabilities.project_memory_sources

    @property
    def project_memory_max_file_chars(self) -> int:
        return self.capabilities.project_memory_max_file_chars

    @property
    def project_memory_max_total_chars(self) -> int:
        return self.capabilities.project_memory_max_total_chars

    @property
    def tool_concurrency_limit(self) -> int | None:
        return self.capabilities.tool_concurrency_limit

    @property
    def aux_idle_timeout_seconds(self) -> float | None:
        return self.capabilities.aux_idle_timeout_seconds

    @property
    def subagent_model_routing(self) -> dict[str, str]:
        return self.capabilities.subagent_model_routing

    @property
    def enable_subagents(self) -> bool:
        return self.subagents.enable_subagents

    @property
    def max_child_runs(self) -> int:
        return self.subagents.max_child_runs

    @property
    def default_child_deadline_seconds(self) -> float | None:
        return self.subagents.default_child_deadline_seconds

    @property
    def code_limits(self):
        return self.code_agent.code_limits

    @property
    def authorized_imports(self) -> tuple[str, ...]:
        return self.code_agent.authorized_imports


@dataclass(slots=True)
class RuntimeStepResult:
    """Internal step transition result."""

    next_step: str


@dataclass(slots=True)
class RunContext:
    """Mutable execution context for one runner loop."""

    run_input: AgentRunInput
    identifiers: dict[str, str]
    metadata: dict[str, Any] = field(default_factory=dict)
    llm_response: LlmResponse | None = None
    prior_checkpoint: CheckpointRef | None = None
    started_at: float = field(default_factory=monotonic)
    # Optional caller-supplied abort signal. Polled at step boundaries
    # (see ``_terminal_from_limits``). Lives outside ``AgentRunInput``
    # because it holds a live threading lock + WeakSet that don't
    # belong in a JSON-serialisable transport contract.
    abort_handle: "RunAbortHandle | None" = None
    # Optional caller-supplied per-call gate (A0.2). Consulted in
    # ``GovernedToolExecutor._execute_one_call`` AFTER the static
    # ``ToolPolicyInput`` pass returns ALLOW; the gate can flip the
    # decision to DENY (blocked envelope) or INTERRUPT (operator
    # approval). Lives on RunContext for the same reason as
    # ``abort_handle`` — callables don't belong on a JSON-serialisable
    # transport contract.
    tool_gate: "ToolGate | None" = None
    # Optional caller-supplied execution backend for THIS run (EPIC-01). A host
    # (e.g. the ACP adapter) injects a per-session backend so the built-in
    # bash/read/write run in that session's prepared environment. Overrides
    # ``RunnerConfig.execution_backend``. Lives on RunContext, not on the
    # JSON-serialisable ``AgentRunInput``, for the same reason as ``abort_handle``.
    execution_backend: "ExecutionBackend | None" = None
    # EPIC-03: the run's execution-lease manager (live; holds the acquired/attached
    # lease). Set inside the drive loop after acquire; the outer run() finally
    # closes it exactly once. Lives here, not on the JSON AgentRunInput, for the
    # same reason as ``execution_backend`` — the durable part is the lease REF in
    # ``metadata["execution_lease_ref"]``, not this live object.
    execution_lease_manager: "ExecutionLeaseManager | None" = None

    @property
    def run_id(self) -> str:
        """Current run identifier."""
        return self.identifiers["run_id"]

    @property
    def attempt_id(self) -> str:
        """Current attempt identifier."""
        return self.identifiers["attempt_id"]

    @property
    def step_name(self) -> str:
        """Current step pointer in deterministic loop."""
        return get_loop_control_state(self).next_step

    @step_name.setter
    def step_name(self, value: str) -> None:
        get_loop_control_state(self).next_step = value

    @property
    def step_count(self) -> int:
        """Executed transition count in current run."""
        return get_loop_control_state(self).step_count

    @step_count.setter
    def step_count(self, value: int) -> None:
        get_loop_control_state(self).step_count = value

    @property
    def tool_calls(self) -> int:
        """Accumulated tool-call count across tool stages."""
        return get_tool_loop_state(self).tool_calls

    @tool_calls.setter
    def tool_calls(self, value: int) -> None:
        get_tool_loop_state(self).tool_calls = value

    @property
    def llm_step_count(self) -> int:
        """Count of completed LLM-call iterations (used for max_steps budget)."""
        return get_loop_control_state(self).llm_step_count

    @llm_step_count.setter
    def llm_step_count(self, value: int) -> None:
        get_loop_control_state(self).llm_step_count = value

    @property
    def attempt_epoch(self) -> int:
        """Monotonic execution-attempt epoch (F1 / U4).

        0 for a fresh run; bumped on each resume that re-drives the run. Tool
        results produced under an attempt are stamped with it so a straggler
        result from a superseded attempt can be fenced (attribution foundation
        for U4 late-result fencing). Metadata-backed so it persists through the
        checkpoint like the other run counters.
        """
        value = self.metadata.get("attempt_epoch")
        return int(value) if isinstance(value, int) else 0

    @attempt_epoch.setter
    def attempt_epoch(self, value: int) -> None:
        self.metadata["attempt_epoch"] = int(value)


def runner_config_parameter_names() -> frozenset[str]:
    """Return every supported keyword name accepted by :class:`RunnerConfig`.

    ``RunnerConfig`` accepts both its direct fields and flattened fields from
    the nested lifecycle settings sections. Host applications can use this
    public helper when adapting a larger legacy settings mapping without
    importing private ``runtime.single_agent`` field sets.
    """

    return frozenset(RunnerConfig.__annotations__) | frozenset(
        _RUNNER_CONFIG_EXTRA_FIELDS
        | _TRIMMING_FIELDS
        | _COMPACTION_FIELDS
        | _SUBAGENT_FIELDS
        | _CODE_AGENT_FIELDS
        | _PYTHON_TOOL_FIELDS
        | _CAPABILITY_FIELDS
    )


@dataclass(frozen=True, slots=True)
class EventSpec:
    """Structured emit spec for runtime events."""

    run_id: str
    attempt_id: str
    event_type: RuntimeEventType
    payload: dict[str, Any] | None = None
    # Epic 037 phase B/C: optional correlation + redaction carriers. When
    # ``trace_id`` is None the emit path defaults it to the deterministic
    # ``run_id:attempt_id`` derivation, so every event correlates to its span
    # without clock-skew. ``redaction`` records that a payload was sanitized.
    trace_id: str | None = None
    severity: EventSeverity | None = None
    redaction: RedactionInfo | None = None


@dataclass(frozen=True, slots=True)
class TerminalResult:
    """Resolved terminal status for one outcome."""

    status: RunStatus
    reason: TerminalReason


@dataclass(frozen=True, slots=True)
class RunnerDeps:
    """External dependencies for the runner loop."""

    provider: LlmProvider
    checkpoint_store: CheckpointStore
    event_log: RuntimeEventLog
    tool_executor: ToolExecutor
    session_store: SessionStore
    artifact_store: ArtifactStore
    context_store: ContextStore
    subagent_store: SubagentStore
    subagent_mailbox_store: SubagentMailboxStore | None
    code_executor: CodeActionExecutor
    tool_registry: ToolRegistry
    command_queue_store: CommandQueueStore | None = None
    # U3 B/C — optional durable CAS ledger that makes approval consumption
    # atomic + exactly-once across concurrent clients / restarts. When None
    # (default), the resume path keeps its TOCTOU + expected-checkpoint guard.
    approval_store: ApprovalConsumptionStore | None = None
    # U4 A/D — optional durable abort lifecycle ledger (requested → observed →
    # cancelled | completed_before_cancel), queryable after restart. None = off.
    abort_store: AbortLifecycleStore | None = None
    # U5 — optional durable store of approved/rejected plan artifacts, written on
    # plan-approval resume so a host has a durable, hash-bound plan record. Off
    # when None (default).
    plan_artifact_store: PlanArtifactStore | None = None
    python_backend: Any | None = None
    lifecycle_hooks: tuple[RunLifecycleHook, ...] = ()
    # Optional providers tried (in order) by the forced-final recovery ladder when the
    # primary provider keeps returning empty finals (deepseek-class quirk). Epic 016;
    # reference: hermes _fallback_chain. Empty tuple = step skipped.
    fallback_providers: tuple[LlmProvider, ...] = ()


@dataclass(slots=True)
class PendingInterruptState:
    """Pending interrupt state kept in checkpoint metadata."""

    interrupt: InterruptRequest
    call: ToolCall
    envelope: ToolResultEnvelope


__all__ = [
    "EventSpec",
    "PendingInterruptState",
    "RunContext",
    "RunnerConfig",
    "runner_config_parameter_names",
    "RunnerDeps",
    "RuntimeStepResult",
    "TerminalResult",
]
