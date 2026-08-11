"""Deep/ultra-agent driver — plan → fan out → artifacts → synthesize (coordination C5).

The long-horizon "deep agent" (LangChain Deep Agents; Anthropic's multi-agent research)
composes four things this SDK already has into one driver:

1. **Plan** — decompose the task into independent subtasks (an LLM planner, or a
   caller-supplied decomposition). The plan is written to ``<workspace>/plan.md``.
2. **Fan out** — one worker per subtask via :func:`run_subagent_group`, sharing the
   workspace (:func:`share_workspace`) so workers and the synthesizer see the same files.
3. **Artifacts** — each worker's findings are persisted and reduced to a light reference
   (:func:`capture_group_artifacts`), the ~15× token fix (C5 step 1).
4. **Synthesize** — a synthesizer child is handed the compact references (not the full
   concatenated findings) and reads the artifact files it needs to write the final answer.

Context compaction already applies for free inside every child's run loop, and the run
loop's own todo/plan ledger governs each child — so this driver is a thin, domain-neutral
composition, not a new engine. Workers and synthesizer run on the parent's provider, tool
registry, and runner config; nothing here is excel- or domain-specific.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from agent_driver.contracts.enums import SubagentJoinPolicy
from agent_driver.runtime.abort import RunAbortHandle
from agent_driver.runtime.tool_gate import ToolGate
from agent_driver.sdk.agent import Agent
from agent_driver.sdk.coordination_events import CoordinationObserver, emit_event
from agent_driver.sdk.artifacts import (
    SubagentArtifact,
    artifact_references,
    capture_group_artifacts,
    share_workspace,
)
from agent_driver.sdk.group import SubagentGroupResult, run_subagent_group
from agent_driver.sdk.subagent import SubagentSpec, run_subagent

# A planner turns the task into a list of independent subtasks (sync or async).
Planner = Callable[[str], Sequence[str] | Awaitable[Sequence[str]]]
# A worker-spec builder turns (index, subtask) into a child spec.
WorkerSpecBuilder = Callable[[int, str], SubagentSpec]

_PLAN_SYSTEM = (
    "You are a planner. Decompose the user's task into a short list of INDEPENDENT "
    "subtasks that can be researched in parallel. Output one subtask per line, no "
    "numbering, no commentary. Fewer, well-scoped subtasks are better than many."
)


@dataclass(frozen=True, slots=True)
class DeepAgentPlan:
    """The decomposition of a task into independent subtasks."""

    task: str
    subtasks: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DeepAgentResult:
    """The outcome of a deep-agent run."""

    task: str
    plan: DeepAgentPlan
    group: SubagentGroupResult
    artifacts: tuple[SubagentArtifact, ...] = ()
    answer: str = ""
    satisfied: bool = False


def _empty_group() -> SubagentGroupResult:
    return SubagentGroupResult(
        results=(), errors=(), join_policy=SubagentJoinPolicy.WAIT_ALL, satisfied=True
    )


def _parse_plan_lines(text: str, *, max_subtasks: int) -> list[str]:
    out: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("-*0123456789.)( ").strip()
        if line:
            out.append(line)
    return out[:max_subtasks]


async def _llm_plan(task: str, provider: object, *, max_subtasks: int) -> list[str]:
    from agent_driver.contracts.messages import ChatMessage
    from agent_driver.llm.aux import aux_completion

    response = await aux_completion(
        provider=provider,
        model=None,
        task="deep_agent_plan",
        temperature=0.0,
        messages=[
            ChatMessage(role="system", content=_PLAN_SYSTEM),
            ChatMessage(role="user", content=task),
        ],
    )
    return _parse_plan_lines(response.message.content or "", max_subtasks=max_subtasks)


async def _resolve_subtasks(
    task: str,
    *,
    planner: Planner | None,
    planner_provider: object | None,
    max_subtasks: int,
) -> list[str]:
    if planner is not None:
        produced = planner(task)
        subs = list(await produced if inspect.isawaitable(produced) else produced)
    elif planner_provider is not None:
        subs = await _llm_plan(task, planner_provider, max_subtasks=max_subtasks)
    else:
        raise ValueError("run_deep_agent needs planner= or planner_provider=")
    return [s.strip() for s in subs if s and s.strip()][:max_subtasks]


def _write_plan_doc(workspace_cwd: str | Path, plan: DeepAgentPlan) -> None:
    root = Path(workspace_cwd)
    root.mkdir(parents=True, exist_ok=True)
    lines = [f"# Plan\n", f"Task: {plan.task}\n"]
    lines += [f"{i}. {s}" for i, s in enumerate(plan.subtasks, start=1)]
    (root / "plan.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def run_deep_agent(
    parent: Agent,
    task: str,
    *,
    workspace_cwd: str | Path,
    planner: Planner | None = None,
    planner_provider: object | None = None,
    worker_spec: WorkerSpecBuilder | None = None,
    synthesizer_agent_type: str = "synthesizer",
    max_subtasks: int = 8,
    join_policy: SubagentJoinPolicy = SubagentJoinPolicy.WAIT_ALL,
    concurrency: int | None = None,
    retries: int = 0,
    include_partial: bool = True,
    artifacts_subdir: str = "artifacts",
    parent_run_id: str | None = None,
    parent_abort_handle: RunAbortHandle | None = None,
    tool_gate: ToolGate | None = None,
    on_event: CoordinationObserver | None = None,
) -> DeepAgentResult:
    """Run one deep-agent pass over ``task``: plan → fan out → artifacts → synthesize.

    The task is decomposed by ``planner`` (a ``(task) -> subtasks`` callable, sync or
    async) or, failing that, an LLM planner using ``planner_provider`` — one is required.
    Each subtask becomes a worker (``worker_spec(index, subtask)``, or a default generic
    spec) fanned out via :func:`run_subagent_group` under ``join_policy`` / ``concurrency``
    / ``retries``, all sharing ``workspace_cwd``. Every worker's findings are captured as an
    artifact; the synthesizer child receives the compact references (with ``include_partial``
    salvaging non-completed workers) and reads the files it needs to produce the final
    ``answer``. An empty plan returns early with ``satisfied=False``. The plan is written to
    ``<workspace_cwd>/plan.md`` for durability.
    """
    subtasks = await _resolve_subtasks(
        task,
        planner=planner,
        planner_provider=planner_provider,
        max_subtasks=max_subtasks,
    )
    plan = DeepAgentPlan(task=task, subtasks=tuple(subtasks))
    _write_plan_doc(workspace_cwd, plan)
    emit_event(
        on_event, "plan_ready",
        total=len(subtasks), detail=f"{len(subtasks)} subtasks",
    )
    if not subtasks:
        return DeepAgentResult(task=task, plan=plan, group=_empty_group())

    specs = [
        worker_spec(i, st)
        if worker_spec is not None
        else SubagentSpec(agent_type=f"worker_{i:02d}", prompt=st)
        for i, st in enumerate(subtasks)
    ]
    group = await run_subagent_group(
        parent,
        share_workspace(specs, workspace_cwd),
        join_policy=join_policy,
        concurrency=concurrency,
        retries=retries,
        parent_run_id=parent_run_id,
        parent_abort_handle=parent_abort_handle,
        tool_gate=tool_gate,
        on_event=on_event,
        phase="workers",
    )
    artifacts = capture_group_artifacts(
        group,
        workspace_cwd=workspace_cwd,
        subdir=artifacts_subdir,
        include_partial=include_partial,
    )

    refs = artifact_references(artifacts)
    synth_prompt = (
        f"Task: {task}\n\n{refs}\n\n"
        "Write the final answer, reading the referenced artifact files as needed."
    )
    synth_spec = share_workspace(
        [SubagentSpec(agent_type=synthesizer_agent_type, prompt=synth_prompt)],
        workspace_cwd,
    )[0]
    emit_event(on_event, "synthesis_started", agent_type=synthesizer_agent_type)
    synth = await run_subagent(
        parent,
        synth_spec,
        parent_run_id=parent_run_id,
        parent_abort_handle=parent_abort_handle,
        tool_gate=tool_gate,
    )
    emit_event(
        on_event, "synthesis_completed",
        agent_type=synthesizer_agent_type, status=synth.status.value, result=synth,
    )
    answer = synth.answer or ""
    return DeepAgentResult(
        task=task,
        plan=plan,
        group=group,
        artifacts=tuple(artifacts),
        answer=answer,
        satisfied=group.satisfied and bool(answer.strip()),
    )


__all__ = [
    "DeepAgentPlan",
    "DeepAgentResult",
    "run_deep_agent",
]
