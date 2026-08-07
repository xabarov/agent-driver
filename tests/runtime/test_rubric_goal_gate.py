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


class _RevisionSequenceProvider(FakeProvider):
    """Return bounded answers and record the tool surface of each request."""

    def __init__(self, answers: list[str]) -> None:
        super().__init__(response_text=answers[-1])
        self._answers = list(answers)
        self.calls = 0
        self.request_tool_names: list[list[str]] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.request_tool_names.append(
            [
                str(tool.get("function", {}).get("name") or tool.get("name") or "")
                for tool in request.tools
            ]
        )
        index = min(self.calls, len(self._answers) - 1)
        self._response_text = self._answers[index]
        self.calls += 1
        return await super().complete(request)


class _RejectBadAnswer(BaseRunLifecycleHook):
    name = "reject_bad_answer"

    async def on_finalize(self, context, *, answer):  # noqa: ANN001
        if answer == "good":
            return None
        return RevisionRequest(
            feedback="Revise the final answer only.",
            disable_tools=True,
            max_revisions=1,
            fail_closed=True,
            gate_id="answer_quality",
        )


@pytest.mark.asyncio
async def test_revision_can_disable_tools_and_then_accept() -> None:
    """A synthesis-only revision hides tools and returns the corrected answer."""
    provider = _RevisionSequenceProvider(["bad", "good"])
    agent = create_agent(
        provider=provider,
        tools=ToolSet.all(),
        lifecycle_hooks=(_RejectBadAnswer(),),
    )

    output = await agent.run(_run_input("r-quality-revised"))

    assert output.status.value == "completed"
    assert output.answer == "good"
    assert provider.request_tool_names[0]
    assert provider.request_tool_names[1] == []
    decisions = [
        event.payload
        for event in output.events
        if event.type.value == "runtime_decision"
        and event.payload.get("kind") == "final_answer"
    ]
    assert len(decisions) == 1
    assert decisions[0]["trigger"] == "finalize"
    assert decisions[0]["action"] == "revise"
    assert decisions[0]["reason"] == "revision_requested"
    assert decisions[0]["status"] == "applied"
    assert decisions[0]["policy_id"] == "answer_quality"
    assert decisions[0]["budget"] == {"revision_count": 1, "max_revisions": 1}
    assert decisions[0]["product_tags"] == ["tools_disabled"]


@pytest.mark.asyncio
async def test_synthesis_revision_is_terminal() -> None:
    """A gate-accepted correction cannot be replaced by an unreviewed third draft."""

    class _AcceptCorrectedAnswer(BaseRunLifecycleHook):
        name = "accept_corrected_answer"

        async def on_finalize(self, context, *, answer):  # noqa: ANN001
            if answer.startswith("corrected"):
                return None
            return RevisionRequest(
                feedback="Produce the corrected terminal synthesis.",
                disable_tools=True,
                max_revisions=1,
                fail_closed=True,
                gate_id="answer_quality",
            )

    corrected = "corrected\n\nNext step: retain this recommendation for later."
    provider = _RevisionSequenceProvider(["bad", corrected, "unreviewed third draft"])
    agent = create_agent(
        provider=provider,
        tools=ToolSet.all(),
        lifecycle_hooks=(_AcceptCorrectedAnswer(),),
    )

    output = await agent.run(_run_input("r-quality-terminal-revision"))

    assert output.status.value == "completed"
    assert output.answer == corrected
    assert provider.calls == 2
    assert provider.request_tool_names[1] == []
    continuation_decisions = [
        event.payload
        for event in output.events
        if event.type.value == "runtime_decision"
        and event.payload.get("policy_id") == "continuation_detector"
    ]
    assert continuation_decisions == []


@pytest.mark.asyncio
async def test_revision_can_fail_closed_after_budget() -> None:
    """A still-invalid revised answer cannot silently pass a fail-closed gate."""
    provider = _RevisionSequenceProvider(["bad", "bad"])
    agent = create_agent(
        provider=provider,
        tools=ToolSet.all(),
        lifecycle_hooks=(_RejectBadAnswer(),),
    )

    output = await agent.run(_run_input("r-quality-blocked"))

    assert output.status.value == "failed"
    assert output.terminal_reason.value == "guardrail_blocked"
    assert provider.calls == 2
    assert provider.request_tool_names[1] == []
    failed = [
        event.payload
        for event in output.events
        if event.type.value == "run_failed"
    ]
    assert failed[-1] == {
        "reason": "guardrail_blocked",
        "policy_id": "answer_quality",
        "revision_count": 1,
    }


def test_revision_request_rejects_invalid_limits_and_gate_ids() -> None:
    with pytest.raises(ValueError, match="max_revisions"):
        RevisionRequest(feedback="x", max_revisions=-1)
    with pytest.raises(ValueError, match="gate_id"):
        RevisionRequest(feedback="x", gate_id="  ")


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


@pytest.mark.asyncio
async def test_finalize_hook_visibility_events_emitted() -> None:
    """A hook overriding on_finalize is bracketed by lifecycle_hook_* events."""

    class _SlowGate(BaseRunLifecycleHook):
        name = "slow_gate"

        async def on_finalize(self, context, *, answer):  # noqa: ANN001
            return None

    agent = create_agent(
        provider=FakeProvider(response_text="done"),
        tools=ToolSet.only(),
        lifecycle_hooks=(_SlowGate(),),
    )
    output = await agent.run(_run_input("r-vis"))
    hook_events = [
        (event.type.value, event.payload)
        for event in output.events
        if event.type.value in {"lifecycle_hook_started", "lifecycle_hook_completed"}
    ]
    started = [p for t, p in hook_events if t == "lifecycle_hook_started"]
    completed = [p for t, p in hook_events if t == "lifecycle_hook_completed"]
    assert [p["hook"] for p in started] == ["slow_gate"]
    assert [p["hook"] for p in completed] == ["slow_gate"]
    assert completed[0]["requested_revision"] is False
    assert completed[0]["phase"] == "finalize"


@pytest.mark.asyncio
async def test_memory_persists_post_revision_answer() -> None:
    """Memory stores the answer the user received, not the pre-revision draft."""
    from agent_driver.memory import InMemoryMemoryStore, StoreBackedMemoryProvider

    class _VaryingProvider(FakeProvider):
        """First completion returns a draft, later ones the final answer."""

        def __init__(self) -> None:
            super().__init__(response_text="draft")
            self.calls = 0

        async def complete(self, request: LlmRequest) -> LlmResponse:
            self.calls += 1
            self._response_text = "draft" if self.calls == 1 else "final"
            return await super().complete(request)

    class _RecordingMemory(StoreBackedMemoryProvider):
        defer_sync = True

        def __init__(self) -> None:
            super().__init__(InMemoryMemoryStore())
            self.synced_answers: list[str | None] = []

        async def sync_turn(self, turn) -> None:  # noqa: ANN001
            self.synced_answers.append(turn.assistant_text)

    class _ReviseFirstDraft(BaseRunLifecycleHook):
        name = "revise_first_draft"

        async def on_finalize(self, context, *, answer):  # noqa: ANN001
            if answer == "draft":
                return RevisionRequest(feedback="not good enough")
            return None

    memory = _RecordingMemory()
    agent = create_agent(
        provider=_VaryingProvider(),
        tools=ToolSet.only(),
        memory_provider=memory,
        lifecycle_hooks=(_ReviseFirstDraft(),),
    )
    output = await agent.run(_run_input("r-rev"))
    await agent.aclose()

    assert output.status.value == "completed"
    assert output.answer == "final"
    assert memory.synced_answers == ["final"]


@pytest.mark.asyncio
async def test_finalize_hook_timeout_fails_open() -> None:
    """A wedged finalize hook is cut at finalize_hook_timeout; answer accepted."""
    import asyncio

    from agent_driver.runtime.single_agent.types import RunnerConfig

    class _WedgedGate(BaseRunLifecycleHook):
        name = "wedged_gate"

        async def on_finalize(self, context, *, answer):  # noqa: ANN001
            await asyncio.sleep(30)
            return RevisionRequest(feedback="should never arrive")

    agent = create_agent(
        provider=FakeProvider(response_text="done"),
        tools=ToolSet.only(),
        lifecycle_hooks=(_WedgedGate(),),
        config=RunnerConfig(finalize_hook_timeout=0.05),
    )
    output = await agent.run(_run_input("r-timeout"))

    assert output.status.value == "completed"  # fail-open: answer accepted
    assert output.answer == "done"
    timed_out = [
        e.payload for e in output.events if e.type.value == "lifecycle_hook_timed_out"
    ]
    assert [p["hook"] for p in timed_out] == ["wedged_gate"]
    assert timed_out[0]["timeout_seconds"] == 0.05
    # No revision loop happened: the timed-out hook's revision was discarded.
    completed = [
        e for e in output.events if e.type.value == "lifecycle_hook_completed"
    ]
    assert completed == []  # timed-out hook does not emit a completed bracket


@pytest.mark.asyncio
async def test_slow_run_start_hook_emits_completed_event() -> None:
    """Non-finalize hooks surface in the journal only when actually slow."""
    import asyncio

    class _SlowStart(BaseRunLifecycleHook):
        name = "slow_start"

        async def on_run_start(self, context):  # noqa: ANN001
            await asyncio.sleep(0.3)

    class _FastStart(BaseRunLifecycleHook):
        name = "fast_start"

        async def on_run_start(self, context):  # noqa: ANN001
            return None

    agent = create_agent(
        provider=FakeProvider(response_text="done"),
        tools=ToolSet.only(),
        lifecycle_hooks=(_SlowStart(), _FastStart()),
    )
    output = await agent.run(_run_input("r-slowstart"))
    run_start_events = [
        e.payload
        for e in output.events
        if e.type.value == "lifecycle_hook_completed"
        and e.payload.get("phase") == "run_start"
    ]
    assert [p["hook"] for p in run_start_events] == ["slow_start"]
    assert run_start_events[0]["duration_ms"] >= 250
