"""Make a coordination run legible (coordination observability).

`run_subagent_group` / `run_coordinator` / `run_deep_agent` return rich result objects,
but answering "what did each worker actually do, and why did one come back empty?" meant
hand-walking nested `tool_trace` / `status` / `terminal_reason` fields. That opacity is a
real cost — a consumer debugging a fan-out had to hand-roll its own per-subagent logging.

This turns that into a first-class, domain-neutral utility. `describe(result)` renders any
coordination result (a `SubagentResult`, `SubagentGroupResult`, `CoordinatorResult`, or
`DeepAgentResult`) as a compact, human-readable trace:

    print(describe(result))
    # coordinator: 2 phases, satisfied=True
    #   phase 'analyze' [wait_all] satisfied=True — 3/3 completed
    #     ✓ data_explorer  completed  4 tools [sheet_overview, read_page, pandas, pandas]  answer=412c  $0.0012
    #     ✓ data_explorer  completed  4 tools […]  answer=0c ⚠empty  $0.0009
    #   phase 'execute' [wait_all] satisfied=True — 1/1 completed
    #     ✗ executor  failed(deadline_exceeded)  7 tools [read_page ×6, pandas]  ⚠no-write  answer=0c ⚠empty

It surfaces the exact things that go wrong in a fan-out — an empty answer, a non-completed
terminal reason, a failed/denied tool call — instead of leaving them buried. For
programmatic checks, `digest_subagent` returns the same facts as a `SubagentDigest`.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from agent_driver.contracts.enums import RunStatus, ToolTraceStatus
from agent_driver.sdk.coordinator import CoordinatorResult
from agent_driver.sdk.deep_agent import DeepAgentResult
from agent_driver.sdk.group import SubagentGroupResult
from agent_driver.sdk.subagent import SubagentResult

_FAILED_TOOL_STATUS = {
    ToolTraceStatus.FAILED,
    ToolTraceStatus.DENIED,
    ToolTraceStatus.TIMED_OUT,
}
_PREVIEW_CHARS = 80


def _enum_value(value: object) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", None) or str(value)


def _preview(text: str) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= _PREVIEW_CHARS else flat[: _PREVIEW_CHARS - 1] + "…"


def _render_tools(tools: tuple[str, ...], *, limit: int = 6) -> str:
    """Compact tool list, collapsing runs of the same tool as ``name ×N``."""
    if not tools:
        return "[]"
    counts = Counter(tools)
    if len(counts) <= limit:
        parts = [name if n == 1 else f"{name} ×{n}" for name, n in counts.items()]
        return "[" + ", ".join(parts) + "]"
    top = counts.most_common(limit)
    parts = [name if n == 1 else f"{name} ×{n}" for name, n in top]
    return "[" + ", ".join(parts) + f", +{len(counts) - limit} more]"


@dataclass(frozen=True, slots=True)
class SubagentDigest:
    """The salient facts of one subagent run — what it did and how it ended."""

    agent_type: str
    status: str
    terminal_reason: str | None
    tool_calls: int
    tools: tuple[str, ...]
    failed_tools: tuple[str, ...]
    answer_chars: int
    answer_preview: str
    cost_usd: float | None
    total_tokens: int

    @property
    def completed(self) -> bool:
        return self.status == RunStatus.COMPLETED.value

    @property
    def empty_answer(self) -> bool:
        return self.answer_chars == 0


def digest_subagent(result: SubagentResult | None) -> SubagentDigest:
    """Reduce a (possibly missing) subagent result to its salient facts."""
    if result is None:
        return SubagentDigest(
            agent_type="<missing>",
            status="MISSING",
            terminal_reason=None,
            tool_calls=0,
            tools=(),
            failed_tools=(),
            answer_chars=0,
            answer_preview="",
            cost_usd=None,
            total_tokens=0,
        )
    trace = result.tool_trace or ()
    tools = tuple(t.tool_name for t in trace)
    failed = tuple(t.tool_name for t in trace if t.status in _FAILED_TOOL_STATUS)
    answer = result.answer or ""
    usage = result.usage
    return SubagentDigest(
        agent_type=result.agent_type,
        status=_enum_value(result.status) or "?",
        terminal_reason=_enum_value(result.terminal_reason),
        tool_calls=len(trace),
        tools=tools,
        failed_tools=failed,
        answer_chars=len(answer),
        answer_preview=_preview(answer),
        cost_usd=(usage.cost_usd_estimate if usage else None),
        total_tokens=(usage.total_tokens if usage else 0),
    )


def describe_subagent(result: SubagentResult | None) -> str:
    """One compact line describing a subagent run, flagging the usual failure modes."""
    d = digest_subagent(result)
    glyph = "✓" if d.completed else "✗"
    reason = (
        f"({d.terminal_reason})"
        if d.terminal_reason and not d.completed
        else ""
    )
    flags = []
    if d.failed_tools:
        flags.append(f"⚠tool-{d.failed_tools[-1]}")
    if d.empty_answer:
        flags.append("⚠empty")
    cost = f"  ${d.cost_usd:.4f}" if d.cost_usd is not None else ""
    flag_str = ("  " + " ".join(flags)) if flags else ""
    return (
        f"{glyph} {d.agent_type}  {d.status}{reason}  {d.tool_calls} tools "
        f"{_render_tools(d.tools)}  answer={d.answer_chars}c{flag_str}{cost}"
    )


def describe_group(group: SubagentGroupResult, *, header: str | None = None, indent: str = "  ") -> str:
    """Multi-line description of a fan-out: policy, satisfaction, and each child."""
    lead = header or (
        f"subagent group [{_enum_value(group.join_policy)}] "
        f"satisfied={group.satisfied} — {group.succeeded}/{len(group.results)} completed"
        + (f", {group.failed} failed" if group.failed else "")
    )
    lines = [lead]
    lines += [indent + describe_subagent(r) for r in group.results]
    return "\n".join(lines)


def describe_coordinator(result: CoordinatorResult) -> str:
    """Per-phase description of a coordinator run."""
    lead = (
        f"coordinator: {len(result.phases)} phases, satisfied={result.satisfied}"
        + (" (stopped early)" if result.stopped_early else "")
    )
    lines = [lead]
    for phase in result.phases:
        header = (
            f"phase '{phase.name}' [{_enum_value(phase.group.join_policy)}] "
            f"satisfied={phase.group.satisfied} — "
            f"{phase.group.succeeded}/{len(phase.group.results)} completed  "
            f"(merged={len(phase.merged)}c)"
        )
        lines.append("  " + header)
        lines += ["    " + describe_subagent(r) for r in phase.group.results]
    return "\n".join(lines)


def describe_deep_agent(result: DeepAgentResult) -> str:
    """Description of a deep-agent run: plan, workers, and the synthesized answer."""
    lines = [
        f"deep_agent: {len(result.plan.subtasks)} subtasks, satisfied={result.satisfied}"
    ]
    for i, subtask in enumerate(result.plan.subtasks, start=1):
        lines.append(f"  {i}. {_preview(subtask)}")
    lines.append("  " + describe_group(result.group, header="workers:", indent="    ").replace("\n", "\n  "))
    lines.append(f"  synthesized answer: {len(result.answer)}c — {_preview(result.answer)}")
    return "\n".join(lines)


def describe(result: object) -> str:
    """Render any coordination result as a compact, human-readable trace.

    Accepts a ``SubagentResult``, ``SubagentGroupResult``, ``CoordinatorResult``, or
    ``DeepAgentResult`` — the one call to reach for when a fan-out didn't do what you
    expected. Falls back to ``repr`` for anything else.
    """
    if isinstance(result, DeepAgentResult):
        return describe_deep_agent(result)
    if isinstance(result, CoordinatorResult):
        return describe_coordinator(result)
    if isinstance(result, SubagentGroupResult):
        return describe_group(result)
    if isinstance(result, SubagentResult) or result is None:
        return describe_subagent(result)
    return repr(result)


__all__ = [
    "SubagentDigest",
    "describe",
    "describe_coordinator",
    "describe_deep_agent",
    "describe_group",
    "describe_subagent",
    "digest_subagent",
]
