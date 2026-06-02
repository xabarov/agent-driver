"""Optional hard gate for Deep Research phase tool order."""

from __future__ import annotations

from dataclasses import dataclass

from agent_driver.runtime.tool_gate import (
    ToolGate,
    ToolGateAllow,
    ToolGateContext,
    ToolGateDeny,
)

DEFAULT_REQUIRED_FETCH_ATTEMPTS = 2

_PHASE_ALLOWED_TOOLS: dict[str, frozenset[str]] = {
    "plan": frozenset({"todo_write", "skill_tool", "skill_view"}),
    "discover": frozenset(
        {
            "agent_tool",
            "skill_tool",
            "skill_view",
            "web_search",
            "web_fetch",
            "glob_search",
            "grep_search",
            "read_file",
            "todo_write",
        }
    ),
    "verify": frozenset({"web_fetch", "web_search", "read_file", "todo_write"}),
    "write": frozenset(
        {
            "file_write",
            "file_edit",
            "file_patch",
            "read_file",
            "artifact_list",
            "artifact_read",
            "artifact_preview",
            "todo_write",
        }
    ),
    "review": frozenset(
        {
            "artifact_list",
            "artifact_preview",
            "artifact_read",
            "read_file",
            "file_patch",
            "file_edit",
            "web_fetch",
            "todo_write",
        }
    ),
}


@dataclass(slots=True)
class DeepResearchPhaseGateState:
    """Stateful phase tracker for one Deep Research run."""

    required_fetch_attempts: int = DEFAULT_REQUIRED_FETCH_ATTEMPTS
    plan_created: bool = False
    search_seen: bool = False
    fetch_attempts: int = 0
    report_written: bool = False

    def phase(self) -> str:
        if not self.plan_created and not self.search_seen:
            return "plan"
        if not self.search_seen:
            return "discover"
        if self.fetch_attempts < max(1, self.required_fetch_attempts):
            return "verify"
        if not self.report_written:
            return "write"
        return "review"

    def observe_allowed_tool(self, tool_name: str, args: dict[str, object]) -> None:
        if tool_name == "todo_write":
            self.plan_created = True
        elif tool_name in {"skill_tool", "skill_view"}:
            return
        elif tool_name == "web_search":
            self.search_seen = True
        elif tool_name == "web_fetch":
            self.fetch_attempts += 1
        elif tool_name == "file_write" and _targets_research_report(args):
            self.report_written = True


def create_deep_research_phase_gate(
    *,
    required_fetch_attempts: int = DEFAULT_REQUIRED_FETCH_ATTEMPTS,
) -> ToolGate:
    """Return a per-run ToolGate enforcing Deep Research phase order."""
    state = DeepResearchPhaseGateState(
        required_fetch_attempts=max(1, int(required_fetch_attempts))
    )

    async def _gate(context: ToolGateContext):
        phase = state.phase()
        allowed = _PHASE_ALLOWED_TOOLS[phase]
        if context.tool_name not in allowed:
            return ToolGateDeny(
                reason=(
                    "deep_research_phase_gate denied "
                    f"{context.tool_name!r} during phase {phase!r}; "
                    f"allowed tools: {', '.join(sorted(allowed))}"
                )
            )
        state.observe_allowed_tool(context.tool_name, context.args)
        return ToolGateAllow(reason=f"deep_research_phase={phase}")

    return _gate


def _targets_research_report(args: dict[str, object]) -> bool:
    value = args.get("path")
    if not isinstance(value, str):
        return True
    normalized = value.strip().replace("\\", "/").rstrip("/")
    return normalized == "research/report.md" or normalized.endswith(
        "/research/report.md"
    )


__all__ = [
    "DEFAULT_REQUIRED_FETCH_ATTEMPTS",
    "DeepResearchPhaseGateState",
    "create_deep_research_phase_gate",
]
