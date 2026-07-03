"""Tests for the OpenInference span emitter — assert spans carry the semantic
conventions Phoenix needs (span kind, input/output, llm/tool attrs, status)."""

from __future__ import annotations

import pytest

# OpenTelemetry is an optional observability extra; skip cleanly when absent.
pytest.importorskip("opentelemetry.sdk.trace.export.in_memory_span_exporter")

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from agent_driver.observability import openinference as oi


@pytest.fixture()
def exporter(monkeypatch):
    """Install an in-memory tracer provider and route oi_span's tracer to it."""
    provider = TracerProvider()
    exp = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exp))
    tracer = provider.get_tracer("test")
    monkeypatch.setattr(oi, "get_otel_tracer", lambda _name: tracer)
    return exp


def _only(exporter):
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    return spans[0]


def test_llm_span_has_kind_io_and_tokens(exporter):
    with oi.oi_span("chat", kind=oi.SPAN_KIND_LLM) as span:
        oi.set_llm(
            span,
            model="qwen/qwen3.5-35b-a3b",
            provider="openrouter",
            invocation_parameters={"temperature": 0.2},
            input_messages=[{"role": "user", "content": "hi"}],
            output_messages=[{"role": "assistant", "content": "hello"}],
            prompt_tokens=10,
            completion_tokens=3,
            total_tokens=13,
            finish_reason="stop",
            prompt_cost=0.001,
            completion_cost=0.002,
            total_cost=0.003,
        )
        oi.set_io(span, input="hi", output="hello")
        oi.record_status(span, ok=True)
    a = _only(exporter).attributes
    assert a["openinference.span.kind"] == "LLM"
    assert a["llm.model_name"] == "qwen/qwen3.5-35b-a3b"
    assert a["llm.token_count.total"] == 13
    assert a["llm.finish_reason"] == "stop"
    assert a["llm.cost.total"] == 0.003
    assert a["llm.input_messages.0.message.content"] == "hi"
    assert a["llm.output_messages.0.message.role"] == "assistant"
    assert a["input.value"] == "hi"
    assert a["output.value"] == "hello"


def test_tool_span_with_error_status(exporter):
    with oi.oi_span("tool", kind=oi.SPAN_KIND_TOOL) as span:
        oi.set_tool(
            span,
            name="chart_vegalite",
            arguments={"spec": {"mark": "bar"}},
            call_id="call_1",
        )
        oi.record_status(
            span, ok=False, description="concurrent operations not permitted"
        )
    s = _only(exporter)
    a = s.attributes
    assert a["openinference.span.kind"] == "TOOL"
    assert a["tool.name"] == "chart_vegalite"
    assert a["tool_call.id"] == "call_1"
    assert "mark" in a["tool_call.function.arguments"]
    assert s.status.status_code.name == "ERROR"
    assert "concurrent" in (s.status.description or "")


def test_agent_span_kind(exporter):
    with oi.oi_span("run", kind=oi.SPAN_KIND_AGENT) as span:
        oi.set_io(span, input={"q": "describe"}, output="done")
    a = _only(exporter).attributes
    assert a["openinference.span.kind"] == "AGENT"
    assert a["input.mime_type"] == "application/json"
    assert a["output.value"] == "done"


def test_agent_subagent_llm_tool_hierarchy_for_phoenix(exporter):
    with oi.oi_span("agent.run", kind=oi.SPAN_KIND_AGENT) as root:
        oi.set_io(root, input="Summarize workbook risk", output="done")
        with oi.oi_span("policy.required_source_evidence", kind=oi.SPAN_KIND_GUARDRAIL):
            pass
        with oi.oi_span("subagent.research", kind=oi.SPAN_KIND_AGENT):
            with oi.oi_span("llm.plan", kind=oi.SPAN_KIND_LLM) as llm:
                oi.set_llm(
                    llm,
                    model="deepseek/deepseek-v4-flash",
                    provider="openrouter",
                    input_messages=[{"role": "user", "content": "Find evidence"}],
                    output_messages=[{"role": "assistant", "content": "Use workbook"}],
                    prompt_tokens=5,
                    completion_tokens=7,
                    total_tokens=12,
                    finish_reason="tool_calls",
                )
            with oi.oi_span("tool.workbook_preview", kind=oi.SPAN_KIND_TOOL) as tool:
                oi.set_tool(
                    tool,
                    name="workbook_preview",
                    arguments={"sheet": "Revenue", "range": "A1:D10"},
                    result={"rows": 10, "redacted": True},
                    call_id="call_workbook_preview",
                )
            with oi.oi_span("llm.final", kind=oi.SPAN_KIND_LLM) as final:
                oi.set_llm(
                    final,
                    model="deepseek/deepseek-v4-flash",
                    provider="openrouter",
                    input_messages=[{"role": "tool", "content": "10 rows"}],
                    output_messages=[{"role": "assistant", "content": "Done"}],
                    prompt_tokens=3,
                    completion_tokens=2,
                    total_tokens=5,
                    finish_reason="stop",
                )

    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert spans["agent.run"].parent is None
    assert spans["policy.required_source_evidence"].parent.span_id == (
        spans["agent.run"].context.span_id
    )
    assert spans["subagent.research"].parent.span_id == spans["agent.run"].context.span_id
    assert spans["llm.plan"].parent.span_id == spans["subagent.research"].context.span_id
    assert spans["tool.workbook_preview"].parent.span_id == (
        spans["subagent.research"].context.span_id
    )
    assert spans["llm.final"].parent.span_id == spans["subagent.research"].context.span_id

    assert spans["agent.run"].attributes["openinference.span.kind"] == "AGENT"
    assert spans["policy.required_source_evidence"].attributes[
        "openinference.span.kind"
    ] == "GUARDRAIL"
    assert spans["llm.plan"].attributes["llm.finish_reason"] == "tool_calls"
    assert spans["llm.final"].attributes["llm.token_count.total"] == 5
    assert spans["tool.workbook_preview"].attributes["tool.name"] == (
        "workbook_preview"
    )


def test_no_op_when_tracing_off(monkeypatch):
    monkeypatch.setattr(oi, "get_otel_tracer", lambda _name: None)
    with oi.oi_span("x", kind=oi.SPAN_KIND_LLM) as span:
        assert span is None
        oi.set_llm(span, model="m")  # must not raise
        oi.set_tool(span, name="t")
        oi.record_status(span, ok=False, description="boom")
