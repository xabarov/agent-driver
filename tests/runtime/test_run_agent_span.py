"""Workstream B — assert the runtime run opens a single OpenInference AGENT
span that wraps the whole step loop and acts as the native parent for nested
LLM/TOOL/subagent spans (so Phoenix groups a run under one trace root)."""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.llm.contracts import LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.observability import openinference as oi
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
    fake_noop_tool_executor,
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
