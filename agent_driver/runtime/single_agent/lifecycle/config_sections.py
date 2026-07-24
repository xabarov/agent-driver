"""Grouped runner configuration sections."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from agent_driver.code_agent.contracts import CodeAgentLimits
from agent_driver.llm.context_windows import (
    resolve_context_window as _resolve_context_window,
)

if TYPE_CHECKING:
    from agent_driver.contracts.profiles import HarnessProfile
    from agent_driver.llm.providers import LlmProvider


@dataclass(frozen=True, slots=True)
class CapabilitySettings:
    """Opt-in capability knobs added by the E1–E8 cross-harness tracks.

    Grouped here (rather than as flat ``RunnerConfig`` fields) so the runner's
    top-level surface stays small and future capabilities have one obvious home.
    ``RunnerConfig`` auto-derives this from flat kwargs and exposes delegating
    properties, so ``RunnerConfig(enable_prompt_cache=True, …)`` and
    ``config.enable_prompt_cache`` keep working unchanged.
    """

    enable_prompt_cache: bool = False
    harness_profiles: tuple["HarnessProfile", ...] = ()
    auxiliary_provider: "LlmProvider | None" = None
    auxiliary_model: str | None = None
    # Epic 032 phase A: per-task aux-model registry (grader / extraction / title / …).
    # ``aux_model_for(task)`` resolves task → auxiliary_models[task] → auxiliary_model
    # → None (provider default). Generalizes the 024 lesson «pick the aux model by
    # measurement» from one env var to a typed seam every side-call shares.
    auxiliary_models: dict[str, str] = field(default_factory=dict)
    project_memory_sources: tuple[str, ...] = ()
    project_memory_max_file_chars: int = 8000
    project_memory_max_total_chars: int = 24000
    tool_concurrency_limit: int | None = None
    subagent_model_routing: dict[str, str] = field(default_factory=dict)
    # Epic 033 A: adaptive tool-deferral threshold. "auto" defers ``should_defer``
    # candidates only when their schemas cross ``tool_defer_threshold_pct`` of the
    # model window (hermes should_activate); "on" always defers (historical); "off"
    # never defers. Inert when no tool is marked ``should_defer``.
    tool_defer_mode: str = "auto"
    tool_defer_threshold_pct: float = 10.0
    # Epic 033 B (tier 3): aggregate per-turn tool-output budget in chars. When a
    # turn's combined tool summaries exceed it, the largest are trimmed (safe_preview
    # + omission marker) until under budget. None/0 = off (historical behaviour).
    per_turn_output_budget_chars: int | None = None

    def __post_init__(self) -> None:
        # Preserve the normalization the flat RunnerConfig assignments used to do
        # (None → empty, coerce containers/bool), now that callers may pass these
        # via either flat kwargs or a constructed CapabilitySettings.
        object.__setattr__(self, "enable_prompt_cache", bool(self.enable_prompt_cache))
        object.__setattr__(self, "harness_profiles", tuple(self.harness_profiles or ()))
        object.__setattr__(
            self, "project_memory_sources", tuple(self.project_memory_sources or ())
        )
        object.__setattr__(
            self, "subagent_model_routing", dict(self.subagent_model_routing or {})
        )
        object.__setattr__(self, "auxiliary_models", dict(self.auxiliary_models or {}))

    def aux_model_for(self, task: str) -> str | None:
        """Resolve the model for an aux ``task`` (epic 032): task registry →
        shared ``auxiliary_model`` → None (provider default)."""
        return self.auxiliary_models.get(task) or self.auxiliary_model


# Class default for the window estimate. A module constant (not type(self).attr):
# slots-dataclass class attributes are member descriptors, not default values.
DEFAULT_CONTEXT_WINDOW_ESTIMATE = 12000


@dataclass(frozen=True, slots=True)
class TrimmingSettings:
    """Context trimming and token pressure thresholds."""

    trim_max_chars: int = 6000
    trim_max_messages: int | None = 24
    trim_max_observations: int | None = 24
    trim_protect_recent_turns: int | None = 4
    microcompact_preserve_recent: int = 6
    microcompact_max_preview_chars: int = 180
    context_window_estimate: int = DEFAULT_CONTEXT_WINDOW_ESTIMATE
    token_warning_threshold: int = 4200
    token_compact_threshold: int = 9000
    token_blocking_threshold: int = 11040
    output_token_reserve: int = 1500
    # Provenance of the window estimate: "default" (class defaults — eligible for
    # per-model auto-resolution), "explicit" (host set it — never overridden),
    # "model_catalog" (auto-resolved via resolve_context_window).
    context_window_source: str = "default"

    def resolved_for_model(self, model: str | None) -> "TrimmingSettings":
        """Auto-scale pressure thresholds to the model's real window (epic 017).

        Only fires when the host left the window at the class default: an explicit
        host-tuned window (via ``for_context_window`` or a direct non-default value)
        always wins. Unknown models keep the defaults. Trim/microcompact fields are
        preserved — only the pressure plane rescales.
        """
        if self.context_window_source != "default":
            return self
        if self.context_window_estimate != DEFAULT_CONTEXT_WINDOW_ESTIMATE:
            return self  # host set a bespoke window without the classmethod
        window = _resolve_context_window(model)
        if window is None or window == self.context_window_estimate:
            return self
        return type(self).for_context_window(
            window,
            trim_max_chars=self.trim_max_chars,
            trim_max_messages=self.trim_max_messages,
            trim_max_observations=self.trim_max_observations,
            trim_protect_recent_turns=self.trim_protect_recent_turns,
            microcompact_preserve_recent=self.microcompact_preserve_recent,
            microcompact_max_preview_chars=self.microcompact_max_preview_chars,
            context_window_source="model_catalog",
        )

    @classmethod
    def for_context_window(
        cls,
        context_window_estimate: int,
        *,
        output_token_reserve: int | None = None,
        **overrides,
    ) -> "TrimmingSettings":
        """Build settings with pressure thresholds scaled to the model's real context window.

        The class defaults describe a 12k window (warning 35%, compact 75%, blocking 92%);
        hosts running large-context models (128k+) must not inherit those absolute numbers —
        a retrieval-heavy prompt then trips compact/blocking far below the model's capacity
        and the run degrades or fails. Ratios are kept, absolute thresholds derive from the
        given window. ``output_token_reserve`` defaults to max(1500, window // 32) so long
        answers are not squeezed on big windows. Any other field passes through ``overrides``.
        """
        window = max(1000, int(context_window_estimate))
        reserve = (
            int(output_token_reserve)
            if output_token_reserve is not None
            else max(1500, window // 32)
        )
        overrides.setdefault("context_window_source", "explicit")
        return cls(
            context_window_estimate=window,
            token_warning_threshold=int(window * 0.35),
            token_compact_threshold=int(window * 0.75),
            token_blocking_threshold=int(window * 0.92),
            output_token_reserve=reserve,
            **overrides,
        )


@dataclass(frozen=True, slots=True)
class CompactionSettings:
    """Compaction orchestration toggles."""

    enable_compaction: bool = False
    enable_session_memory_compaction: bool = False
    enable_llm_compaction: bool = False
    enable_partial_compaction: bool = True
    enable_ptl_retry: bool = True
    compaction_failure_limit: int = 3
    session_memory_stale_after_turns: int = 4
    compaction_model: str = "default"
    ptl_retry_max_chars: int = 4000
    post_compact_max_reinjected_artifact_refs: int = 5
    enable_tool_arg_truncation: bool = False
    tool_arg_truncation_max_chars: int = 2000
    # Epic 035 A: tiered compression of OLD tool-result bulk (stub/truncate by tier)
    # for stateless/no-cache providers. LLM-free, idempotent, structure-preserving.
    # Default off — enable on a fallback to a no-prompt-cache backend.
    enable_tool_history_compression: bool = False


@dataclass(frozen=True, slots=True)
class SubagentSettings:
    """Subagent fan-out limits."""

    enable_subagents: bool = True
    max_child_runs: int = 8
    default_child_deadline_seconds: float | None = 90.0
    # Epic 019 phase C: config-level child step/tool budgets. None keeps the
    # executor's built-in defaults (8 steps / 6 tool calls); a planned task's own
    # metadata["max_steps"/"max_tool_calls"] always wins.
    default_child_max_steps: int | None = None
    default_child_max_tool_calls: int | None = None


@dataclass(frozen=True, slots=True)
class CodeAgentSettings:
    """Code-agent profile execution settings."""

    code_limits: CodeAgentLimits = field(default_factory=CodeAgentLimits)
    authorized_imports: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PythonToolSettings:
    """Python tool execution settings."""

    enabled: bool = False
    backend: str = "local"
    include_scientific_stack: bool = True
    default_imports: tuple[str, ...] = ()
    allow_overlay: bool = False
    limits: CodeAgentLimits = field(default_factory=CodeAgentLimits)
    session_idle_seconds: float = 300.0


__all__ = [
    "CapabilitySettings",
    "CodeAgentSettings",
    "CompactionSettings",
    "PythonToolSettings",
    "SubagentSettings",
    "TrimmingSettings",
]
