"""D3 — rubric goal-gate: on_finalize revision loop + RubricLifecycleHook."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput
from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import GraderVerdict, RubricGradeInput, RubricLifecycleHook
from agent_driver.runtime.lifecycle_hooks import BaseRunLifecycleHook, RevisionRequest
from agent_driver.sdk import ToolSet, create_agent


def _run_input(run_id: str) -> AgentRunInput:
    return AgentRunInput(
        input="write something",
        run_id=run_id,
        thread_id="t1",
        agent_id="agent",
        graph_preset="single_react",
    )


@pytest.mark.asyncio
async def test_revision_request_loops_then_accepts() -> None:
    """A hook that revises once then accepts makes the run loop exactly once."""

    class _ReviseOnce(BaseRunLifecycleHook):
        name = "revise_once"

        def __init__(self) -> None:
            self.finalizes = 0

        async def on_finalize(self, context, *, answer):  # noqa: ANN001
            self.finalizes += 1
            if self.finalizes == 1:
                return RevisionRequest(feedback="needs more detail")
            return None

    hook = _ReviseOnce()
    agent = create_agent(
        provider=FakeProvider(response_text="done"),
        tools=ToolSet.only(),
        lifecycle_hooks=(hook,),
    )
    output = await agent.run(_run_input("r1"))
    assert output.status.value == "completed"
    assert hook.finalizes == 2  # first revised, second accepted


class _CapturingProvider(FakeProvider):
    """Records the concatenated user-message text of each request."""

    def __init__(self) -> None:
        super().__init__(response_text="draft answer")
        self.user_text: list[str] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.user_text.append(
            " ".join(m.content for m in request.messages if m.role == "user")
        )
        return await super().complete(request)


@pytest.mark.asyncio
async def test_rubric_hook_revises_with_feedback_then_passes() -> None:
    """Grader needs_revision once; its feedback reaches the model; then it passes."""
    grade_calls: list[int] = []

    async def grade(inp: RubricGradeInput) -> GraderVerdict:
        grade_calls.append(inp.iteration)
        if inp.iteration == 0:
            return GraderVerdict(satisfied=False, feedback="cite a source")
        return GraderVerdict(satisfied=True)

    provider = _CapturingProvider()
    agent = create_agent(
        provider=provider,
        tools=ToolSet.only(),
        lifecycle_hooks=(RubricLifecycleHook("must cite sources", grade),),
    )
    output = await agent.run(_run_input("r_rubric"))

    assert output.status.value == "completed"
    assert grade_calls == [0, 1]  # graded twice: revise, then satisfied
    # The grader's feedback was injected as a user turn the model saw.
    assert any("cite a source" in text for text in provider.user_text)


@pytest.mark.asyncio
async def test_rubric_hook_bounded_by_max_iterations() -> None:
    """A never-satisfied grader stops after max_iterations and accepts."""
    grade_calls: list[int] = []

    async def grade(inp: RubricGradeInput) -> GraderVerdict:
        grade_calls.append(inp.iteration)
        return GraderVerdict(satisfied=False, feedback="still not good")

    agent = create_agent(
        provider=FakeProvider(response_text="x"),
        tools=ToolSet.only(),
        lifecycle_hooks=(RubricLifecycleHook("impossible", grade, max_iterations=2),),
    )
    output = await agent.run(_run_input("r_cap"))

    assert output.status.value == "completed"  # accepts after the budget
    assert len(grade_calls) == 2  # graded exactly max_iterations times
