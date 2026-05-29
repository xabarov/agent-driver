"""Offline real-scenario regressions (text-form recovery, interrupt resume, session store)."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput, ResumeAction, ToolCall, ToolRisk
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmResponse, UsageSummary
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.llm.tool_call_parser import strip_text_form_tool_calls
from agent_driver.context import InMemorySessionStore
from agent_driver.runtime import InMemoryCheckpointStore, InMemoryEventLog
from agent_driver.runtime.single_agent.types import RunnerConfig
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet


class _TextFormThenAnswerProvider(FakeProvider):
    """First turn: text-form read_file; second: final answer without markup."""

    def __init__(self) -> None:
        super().__init__(response_text="unused")
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            return LlmResponse(
                message=ChatMessage(
                    role="assistant",
                    content=(
                        "Reading README.\n"
                        '<tool_call>{"name":"glob_search","arguments":{"pattern":"README.md"}}</tool_call>'
                    ),
                ),
                finish_reason=LlmFinishReason.STOP,
                usage=UsageSummary(model_provider="textform", model_name="test"),
                provider="textform",
                model="test-model",
            )
        return LlmResponse(
            message=ChatMessage(role="assistant", content="README path is README.md"),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="textform", model_name="test"),
            provider="textform",
            model="test-model",
        )


@pytest.mark.asyncio
async def test_text_form_tool_call_recovery_executes_tool_and_cleans_answer() -> None:
    """Regression for text-form parsing loop + final answer strip."""
    provider = _TextFormThenAnswerProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only("glob_search"))
    output = await agent.run(
        AgentRunInput(
            input="find README",
            run_id="run_text_form_recovery",
            agent_id="agent",
            graph_preset="single_react",
            max_steps=12,
            max_tool_calls=6,
        )
    )
    assert output.answer == "README path is README.md"
    assert "<tool_call>" not in (output.answer or "")
    assert any(row.tool_name == "glob_search" for row in output.tool_trace)
    assert len(provider.requests) == 2


@pytest.mark.asyncio
async def test_interrupt_resume_approve_executes_pending_bash(tmp_path) -> None:
    """Paused run should resume and execute approved bash once."""
    target = tmp_path / "resume-approve.txt"

    class _InterruptThenStop(FakeProvider):
        def __init__(self) -> None:
            super().__init__(response_text="done")
            self.calls = 0

        async def complete(self, request: LlmRequest) -> LlmResponse:
            self.calls += 1
            if self.calls == 1:
                return LlmResponse(
                    message=ChatMessage(role="assistant", content=""),
                    finish_reason=LlmFinishReason.TOOL_CALLS,
                    usage=UsageSummary(model_provider="fake", model_name="test"),
                    provider="fake",
                    model="test-model",
                    metadata={
                        "planned_tool_calls": [
                            ToolCall(
                                tool_name="file_write",
                                args={"path": str(target), "content": "approved\n"},
                            ).model_dump(mode="json")
                        ]
                    },
                )
            return LlmResponse(
                message=ChatMessage(role="assistant", content="write completed"),
                finish_reason=LlmFinishReason.STOP,
                usage=UsageSummary(model_provider="fake", model_name="test"),
                provider="fake",
                model="test-model",
            )

    provider = _InterruptThenStop()
    agent = create_agent(
        provider=provider,
        tools=ToolSet.only("file_write"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
    )
    paused = await agent.run(
        AgentRunInput(
            input="write file",
            run_id="run_interrupt_resume",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy={"approval_required_for_risk": ToolRisk.MEDIUM.value},
        )
    )
    assert paused.status.value == "paused"
    assert paused.interrupt is not None
    resumed = await agent.resume(
        run_id=paused.run_id,
        interrupt_id=paused.interrupt.interrupt_id,
        action=ResumeAction.APPROVE,
    )
    assert resumed.status.value == "completed"
    assert target.read_text(encoding="utf-8") == "approved\n"


@pytest.mark.asyncio
async def test_session_resume_from_store_persists_digest_across_runs() -> None:
    """Two runs with same thread_id should persist session digests in store."""
    session_store = InMemorySessionStore()

    class _OneShotProvider(FakeProvider):
        def __init__(self, *, answer: str) -> None:
            super().__init__(response_text=answer)

    thread_id = "thread_session_resume_test"
    agent = create_agent(
        provider=_OneShotProvider(answer="discussed agent_driver/cli/main.py"),
        tools=ToolSet.only("glob_search"),
        config=RunnerConfig(session_store=session_store),
    )
    first = await agent.run(
        AgentRunInput(
            input="find main.py",
            run_id="run_session_first",
            thread_id=thread_id,
            agent_id="agent",
            graph_preset="single_react",
            max_steps=4,
            max_tool_calls=2,
            deadline_seconds=30.0,
        )
    )
    assert first.status.value == "completed"
    runner_store = agent.runner._deps.session_store
    assert isinstance(first.metadata, dict)
    assert first.metadata.get("digest_refs")
    digests = runner_store.list_digests(thread_id)
    assert digests

    second = await agent.run(
        AgentRunInput(
            input="continue without repeating filename",
            run_id="run_session_second",
            thread_id=thread_id,
            agent_id="agent",
            graph_preset="single_react",
            max_steps=4,
            max_tool_calls=2,
            deadline_seconds=30.0,
        )
    )
    assert second.status.value == "completed"
    assert len(runner_store.list_digests(thread_id)) >= len(digests)


def test_answer_matches_expectations_any_of_groups() -> None:
    """Eval scoring helper should treat any_of groups as OR within AND."""
    from agent_driver.cli.evals import EvalScenario, _answer_matches_expectations

    scenario = EvalScenario(
        scenario_id="x",
        prompt="p",
        expected_answer_any_of=(("not found", "не найдено"), ("main.py",)),
    )
    assert _answer_matches_expectations(
        answer="Совпадений не найдено в main.py", scenario=scenario
    )
    assert not _answer_matches_expectations(answer="ok", scenario=scenario)


@pytest.mark.asyncio
async def test_second_run_receives_prior_user_message_in_llm_request() -> None:
    """Same thread_id: second run should include prior user turn in protocol messages."""
    session_store = InMemorySessionStore()

    class _CapturingProvider(FakeProvider):
        def __init__(self) -> None:
            super().__init__(response_text="done")
            self.requests: list[LlmRequest] = []

        async def complete(self, request: LlmRequest) -> LlmResponse:
            self.requests.append(request)
            return await super().complete(request)

    provider = _CapturingProvider()
    thread_id = "thread_prompt_memory"
    agent = create_agent(
        provider=provider,
        tools=ToolSet.only("glob_search"),
        config=RunnerConfig(session_store=session_store),
    )
    await agent.run(
        AgentRunInput(
            input="first turn marker ALPHA",
            run_id="run_mem_1",
            thread_id=thread_id,
            agent_id="agent",
            graph_preset="single_react",
            max_steps=4,
            max_tool_calls=2,
        )
    )
    await agent.run(
        AgentRunInput(
            input="second turn",
            run_id="run_mem_2",
            thread_id=thread_id,
            messages=(ChatMessage(role="user", content="first turn marker ALPHA"),),
            agent_id="agent",
            graph_preset="single_react",
            max_steps=4,
            max_tool_calls=2,
        )
    )
    assert len(provider.requests) >= 2
    first_contents = [
        msg.content for msg in provider.requests[0].messages if msg.content
    ]
    assert any("ALPHA" in (content or "") for content in first_contents)
    second_contents = [
        msg.content for msg in provider.requests[-1].messages if msg.content
    ]
    assert any("second turn" in (content or "") for content in second_contents)


def test_strip_text_form_does_not_remove_valid_answer_text() -> None:
    """Stripping should only remove tool-call blocks, not normal prose."""
    raw = "Result: welcome\n<tool_call>{\"name\":\"read_file\"}</tool_call>"
    cleaned = strip_text_form_tool_calls(raw)
    assert cleaned == "Result: welcome"
