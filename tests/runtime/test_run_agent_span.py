"""Workstream B — assert the runtime run opens a single OpenInference AGENT
span that wraps the whole step loop and acts as the native parent for nested
LLM/TOOL/subagent spans (so Phoenix groups a run under one trace root)."""

from __future__ import annotations

import pytest

# OpenTelemetry is an optional observability extra; skip cleanly when absent.
pytest.importorskip("opentelemetry.sdk.trace.export.in_memory_span_exporter")

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.contracts.tools import ToolCall
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmResponse
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.observability import openinference as oi
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
    fake_noop_tool_executor,
)
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet


class _ParallelToolsThenFinalProvider(FakeProvider):
    """First turn asks for real governed tools, second turn returns final answer."""

    def __init__(self) -> None:
        super().__init__(response_text="unused")
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        usage = UsageSummary(
            input_tokens=7,
            output_tokens=3,
            total_tokens=10,
            model_provider="fake",
            model_name=request.model or "fake-model",
        )
        if len(self.requests) == 1:
            return LlmResponse(
                message=ChatMessage(role="assistant", content=""),
                finish_reason=LlmFinishReason.TOOL_CALLS,
                usage=usage,
                provider="fake",
                model=request.model or "fake-model",
                metadata={
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="glob_search",
                            tool_call_id="glob_readme",
                            args={"pattern": "README.md"},
                        ).model_dump(mode="json"),
                        ToolCall(
                            tool_name="glob_search",
                            tool_call_id="glob_pyproject",
                            args={"pattern": "pyproject.toml"},
                        ).model_dump(mode="json"),
                    ]
                },
            )
        return LlmResponse(
            message=ChatMessage(role="assistant", content="found both files"),
            finish_reason=LlmFinishReason.STOP,
            usage=usage,
            provider="fake",
            model=request.model or "fake-model",
        )


@pytest.fixture()
def exporter(monkeypatch):
    """Route every ``oi_span`` to an in-memory tracer for assertions."""
    provider = TracerProvider()
    exp = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exp))
    tracer = provider.get_tracer("test")
    monkeypatch.setattr(oi, "get_otel_tracer", lambda _name: tracer)
    return exp


def _build_runner(executor) -> FakeSingleStepRunner:
    return FakeSingleStepRunner(
        provider=FakeProvider(response_text="the answer"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(tool_executor=executor),
    )


@pytest.mark.asyncio
async def test_run_opens_agent_span_with_io_and_status(exporter) -> None:
    runner = _build_runner(fake_noop_tool_executor)

    out = await runner.run(
        AgentRunInput(
            input="hello there",
            run_id="run_agent_span_1",
            agent_id="agent-test",
            graph_preset="single_react",
            model_role="default",
        )
    )

    run_spans = [s for s in exporter.get_finished_spans() if s.name == "agent.run"]
    assert len(run_spans) == 1
    span = run_spans[0]
    assert span.attributes["openinference.span.kind"] == "AGENT"
    assert span.attributes["agent.id"] == "agent-test"
    assert span.attributes["input.value"] == "hello there"
    # Output panel mirrors the run's final answer.
    assert span.attributes["output.value"] == out.answer
    # A completed run carries OK status (no error description).
    assert span.status.is_ok


@pytest.mark.asyncio
async def test_nested_child_span_parents_to_the_run_span(exporter) -> None:
    """A span opened mid-run (tool/LLM/subagent) nests under ``agent.run``."""

    async def _executor(run_input: AgentRunInput, llm_response: LlmResponse):
        with oi.oi_span("child.tool", kind=oi.SPAN_KIND_TOOL):
            pass
        return await fake_noop_tool_executor(run_input, llm_response)

    runner = _build_runner(_executor)

    await runner.run(
        AgentRunInput(
            input="hi",
            run_id="run_agent_span_2",
            agent_id="agent-test",
            graph_preset="single_react",
        )
    )

    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert "agent.run" in spans and "child.tool" in spans
    # Native parenting: the child's parent is the run span — one trace root.
    assert spans["child.tool"].parent is not None
    assert spans["child.tool"].parent.span_id == spans["agent.run"].context.span_id


@pytest.mark.asyncio
async def test_streaming_llm_span_parents_to_the_run_span(exporter) -> None:
    """Streaming chunks are collected inside the same LLM child span."""

    runner = _build_runner(fake_noop_tool_executor)

    await runner.run(
        AgentRunInput(
            input="stream it",
            run_id="run_agent_span_stream",
            agent_id="agent-test",
            graph_preset="single_react",
            stream=True,
        )
    )

    spans = exporter.get_finished_spans()
    run_spans = [s for s in spans if s.name == "agent.run"]
    llm_spans = [
        s for s in spans if s.attributes.get("openinference.span.kind") == "LLM"
    ]
    assert len(run_spans) == 1
    assert len(llm_spans) == 1
    assert llm_spans[0].context.trace_id == run_spans[0].context.trace_id
    assert llm_spans[0].parent is not None
    assert llm_spans[0].parent.span_id == run_spans[0].context.span_id


@pytest.mark.asyncio
async def test_real_runner_llm_and_parallel_tool_spans_parent_to_run_span(
    exporter,
) -> None:
    """Actual SDK runtime keeps LLM and asyncio-gather tool spans under agent.run."""

    provider = _ParallelToolsThenFinalProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only("glob_search"))

    out = await agent.run(
        AgentRunInput(
            input="find repo metadata files",
            run_id="run_real_hierarchy",
            agent_id="agent-hierarchy",
            graph_preset="single_react",
            max_steps=8,
            max_tool_calls=4,
        )
    )

    assert out.status.value == "completed"
    assert out.answer == "found both files"
    assert len(provider.requests) == 2

    spans = exporter.get_finished_spans()
    run_spans = [s for s in spans if s.name == "agent.run"]
    llm_spans = [
        s for s in spans if s.attributes.get("openinference.span.kind") == "LLM"
    ]
    tool_spans = [
        s for s in spans if s.attributes.get("openinference.span.kind") == "TOOL"
    ]

    assert len(run_spans) == 1
    assert len(llm_spans) == 2
    assert len(tool_spans) == 2
    run_span = run_spans[0]

    for span in [*llm_spans, *tool_spans]:
        assert span.context.trace_id == run_span.context.trace_id
        assert span.parent is not None
        assert span.parent.span_id == run_span.context.span_id

    assert {s.attributes["tool.name"] for s in tool_spans} == {"glob_search"}
    assert {
        s.attributes["tool_call.function.name"] for s in tool_spans
    } == {"glob_search"}
    assert {
        s.attributes["tool_call.id"] for s in tool_spans
    } == {"glob_readme", "glob_pyproject"}
    assert all(s.attributes["llm.token_count.total"] == 10 for s in llm_spans)
