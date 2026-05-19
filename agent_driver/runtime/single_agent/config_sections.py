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
    token_warning_threshold: int = 7500
    token_compact_threshold: int = 9000
    token_blocking_threshold: int = 10500
    output_token_reserve: int = 1500


@dataclass(frozen=True, slots=True)
class CompactionSettings:
    """Compaction orchestration toggles."""

    enable_compaction: bool = False
    enable_session_memory_compaction: bool = False
    enable_llm_compaction: bool = False
    compaction_failure_limit: int = 3
    session_memory_stale_after_turns: int = 4
    compaction_model: str = "default"


@dataclass(frozen=True, slots=True)
class SubagentSettings:
    """Subagent fan-out limits."""

    enable_subagents: bool = False
    max_child_runs: int = 8
    default_child_deadline_seconds: float | None = 90.0


@dataclass(frozen=True, slots=True)
class CodeAgentSettings:
    """Code-agent profile execution settings."""

    code_limits: CodeAgentLimits = field(default_factory=CodeAgentLimits)
    authorized_imports: tuple[str, ...] = ()


__all__ = [
    "CodeAgentSettings",
    "CompactionSettings",
    "SubagentSettings",
    "TrimmingSettings",
]
