"""Grouped runner configuration sections."""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_driver.code_agent.contracts import CodeAgentLimits


@dataclass(frozen=True, slots=True)
class TrimmingSettings:
    """Context trimming and token pressure thresholds."""

    trim_max_chars: int = 6000
    trim_max_messages: int | None = 24
    trim_max_observations: int | None = 24
    microcompact_preserve_recent: int = 6
    microcompact_max_preview_chars: int = 180
    context_window_estimate: int = 12000
    token_warning_threshold: int = 4200
    token_compact_threshold: int = 9000
    token_blocking_threshold: int = 11040
    output_token_reserve: int = 1500


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


@dataclass(frozen=True, slots=True)
class SubagentSettings:
    """Subagent fan-out limits."""

    enable_subagents: bool = True
    max_child_runs: int = 8
    default_child_deadline_seconds: float | None = 90.0


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
    "CodeAgentSettings",
    "CompactionSettings",
    "PythonToolSettings",
    "SubagentSettings",
    "TrimmingSettings",
]
