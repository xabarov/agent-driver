"""Rubric goal-gate: grade the final answer and iterate until it passes.

A lifecycle hook that, at finalize, scores the run's answer against
caller-supplied criteria via a host-provided grader; if not satisfied it
returns a :class:`RevisionRequest` so the runtime injects the feedback and
resumes the run. Iteration is bounded by ``max_iterations`` here and by a hard
backstop in the step loop.

Grading stays the host's responsibility (a separate grader subagent, a
structured LLM call, a test runner) via the ``grade`` callback — the runtime
does not prescribe how "done" is judged, mirroring deepagents' RubricMiddleware
but on agent-driver's lifecycle seam.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent_driver.runtime.lifecycle_hooks import BaseRunLifecycleHook, RevisionRequest
from agent_driver.runtime.metadata_state import get_rubric_runtime_state

if TYPE_CHECKING:
    from agent_driver.runtime.single_agent.types import RunContext


@dataclass(frozen=True, slots=True)
class RubricGradeInput:
    """What the grader sees: the criteria, the current answer, the iteration."""

    criteria: str
    answer: str
    iteration: int


@dataclass(frozen=True, slots=True)
class GraderVerdict:
    """The grader's decision; ``feedback`` guides the next attempt if not satisfied."""

    satisfied: bool
    feedback: str = ""


GradeFn = Callable[[RubricGradeInput], Awaitable[GraderVerdict]]


class RubricLifecycleHook(BaseRunLifecycleHook):
    """Iterate the run toward caller-supplied criteria via a grader callback."""

    name = "rubric"

    def __init__(
        self, criteria: str, grade: GradeFn, *, max_iterations: int = 3
    ) -> None:
        self._criteria = criteria
        self._grade = grade
        self._max_iterations = max_iterations

    async def on_finalize(
        self, context: "RunContext", *, answer: str
    ) -> RevisionRequest | None:
        state = get_rubric_runtime_state(context)
        if state.iterations() >= self._max_iterations:
            return None  # budget exhausted — accept the current answer
        verdict = await self._grade(
            RubricGradeInput(
                criteria=self._criteria, answer=answer, iteration=state.iterations()
            )
        )
        state.record_evaluation(satisfied=verdict.satisfied, feedback=verdict.feedback)
        if verdict.satisfied:
            return None
        return RevisionRequest(
            feedback=verdict.feedback or "Revise the answer to meet the criteria."
        )


__all__ = ["GradeFn", "GraderVerdict", "RubricGradeInput", "RubricLifecycleHook"]
