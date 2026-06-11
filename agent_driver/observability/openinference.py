"""OpenInference-compliant span helpers for rich Phoenix agent traces.

Phoenix renders spans richly (colored AGENT/LLM/TOOL kinds, Input/Output panels,
token counts, cost, error Status Description) ONLY when they carry the
OpenInference semantic-convention attributes. Without them every span is a generic
gray JSON box. These helpers set those attributes consistently.

Spec: https://arize-ai.github.io/openinference/spec/semantic_conventions.html
Everything here is a no-op when tracing is off and never raises — telemetry must
never break a run.
"""

from __future__ import annotations

import contextlib
from typing import Any, Iterable, Mapping

from agent_driver.observability.phoenix import get_otel_tracer, safe_json

_TRACER_NAME = "agent_driver"

# Literal OpenInference attribute keys (stable per the spec). Mirrors
# openinference.semconv.trace.SpanAttributes — kept as literals so this module
# has no hard dependency on the constants package being importable.
_SPAN_KIND = "openinference.span.kind"
_INPUT_VALUE = "input.value"
_INPUT_MIME = "input.mime_type"
_OUTPUT_VALUE = "output.value"
_OUTPUT_MIME = "output.mime_type"
_LLM_MODEL = "llm.model_name"
_LLM_PROVIDER = "llm.provider"
_LLM_SYSTEM = "llm.system"
_LLM_INVOCATION = "llm.invocation_parameters"
_LLM_TOK_PROMPT = "llm.token_count.prompt"
_LLM_TOK_COMPLETION = "llm.token_count.completion"
_LLM_TOK_TOTAL = "llm.token_count.total"
_TOOL_NAME = "tool.name"
_TOOL_DESCRIPTION = "tool.description"
_TOOL_PARAMETERS = "tool.parameters"
_TOOL_CALL_ID = "tool_call.id"
_TOOL_FN_NAME = "tool_call.function.name"
_TOOL_FN_ARGS = "tool_call.function.arguments"

# Valid span kinds (upper-case per spec).
SPAN_KIND_AGENT = "AGENT"
SPAN_KIND_CHAIN = "CHAIN"
SPAN_KIND_LLM = "LLM"
SPAN_KIND_TOOL = "TOOL"
SPAN_KIND_RETRIEVER = "RETRIEVER"
SPAN_KIND_GUARDRAIL = "GUARDRAIL"


def _set(span: Any, key: str, value: Any) -> None:
    if span is None or value is None:
        return
    try:
        span.set_attribute(key, value)
    except Exception:  # telemetry must never break a run
        pass


def _as_value(payload: Any) -> tuple[str, str]:
    """Return (value, mime_type) for an input/output payload."""
    if isinstance(payload, str):
        return payload, "text/plain"
    return safe_json(payload), "application/json"


@contextlib.contextmanager
def oi_span(
    name: str,
    *,
    kind: str,
    attributes: Mapping[str, Any] | None = None,
    tracer_name: str = _TRACER_NAME,
):
    """Open an OpenInference span of ``kind`` (AGENT/LLM/TOOL/CHAIN/…).

    Yields the span (or ``None`` when tracing is off) for further enrichment via
    :func:`set_io` / :func:`set_llm` / :func:`set_tool` / :func:`record_status`.
    Nests natively under whatever span is current.
    """
    tracer = get_otel_tracer(tracer_name)
    if tracer is None:
        yield None
        return
    attrs = dict(attributes or {})
    attrs[_SPAN_KIND] = kind.upper()
    try:
        cm = tracer.start_as_current_span(name, attributes=attrs)
    except Exception:  # tracing must never break a run
        yield None
        return
    with cm as span:
        yield span


def set_io(span: Any, *, input: Any = None, output: Any = None) -> None:
    """Set Phoenix Input / Output panels (``input.value`` / ``output.value``)."""
    if span is None:
        return
    if input is not None:
        value, mime = _as_value(input)
        _set(span, _INPUT_VALUE, value)
        _set(span, _INPUT_MIME, mime)
    if output is not None:
        value, mime = _as_value(output)
        _set(span, _OUTPUT_VALUE, value)
        _set(span, _OUTPUT_MIME, mime)


def set_llm(
    span: Any,
    *,
    model: str | None = None,
    provider: str | None = None,
    system: str | None = None,
    invocation_parameters: Any = None,
    input_messages: Iterable[Mapping[str, Any]] | None = None,
    output_messages: Iterable[Mapping[str, Any]] | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
) -> None:
    """Set LLM semantic attributes (model, provider, params, messages, tokens).

    ``llm.token_count.*`` is what unlocks both token display AND cost in Phoenix.
    """
    if span is None:
        return
    _set(span, _LLM_MODEL, model)
    _set(span, _LLM_PROVIDER, provider)
    _set(span, _LLM_SYSTEM, system)
    if invocation_parameters is not None:
        _set(span, _LLM_INVOCATION, safe_json(invocation_parameters))
    _set(span, _LLM_TOK_PROMPT, prompt_tokens)
    _set(span, _LLM_TOK_COMPLETION, completion_tokens)
    _set(span, _LLM_TOK_TOTAL, total_tokens)
    for i, msg in enumerate(input_messages or []):
        _set(span, f"llm.input_messages.{i}.message.role", msg.get("role"))
        _set(span, f"llm.input_messages.{i}.message.content", msg.get("content"))
    for i, msg in enumerate(output_messages or []):
        _set(span, f"llm.output_messages.{i}.message.role", msg.get("role"))
        _set(span, f"llm.output_messages.{i}.message.content", msg.get("content"))


def set_tool(
    span: Any,
    *,
    name: str | None = None,
    description: str | None = None,
    parameters: Any = None,
    arguments: Any = None,
    result: Any = None,
    call_id: str | None = None,
) -> None:
    """Set TOOL semantic attributes; also mirrors args/result to Input/Output."""
    if span is None:
        return
    _set(span, _TOOL_NAME, name)
    _set(span, _TOOL_DESCRIPTION, description)
    _set(span, _TOOL_CALL_ID, call_id)
    if name:
        _set(span, _TOOL_FN_NAME, name)
    if parameters is not None:
        _set(span, _TOOL_PARAMETERS, safe_json(parameters))
    if arguments is not None:
        _set(span, _TOOL_FN_ARGS, safe_json(arguments))
        set_io(span, input=arguments)
    if result is not None:
        set_io(span, output=result)


def record_status(
    span: Any,
    *,
    ok: bool,
    description: str | None = None,
    exception: BaseException | None = None,
) -> None:
    """Set OTel span status — Phoenix shows ERROR + the description prominently."""
    if span is None:
        return
    try:
        from opentelemetry.trace import Status, StatusCode

        if ok:
            span.set_status(Status(StatusCode.OK))
        else:
            span.set_status(Status(StatusCode.ERROR, description or ""))
            if exception is not None:
                span.record_exception(exception)
    except Exception:  # telemetry must never break a run
        pass


__all__ = [
    "oi_span",
    "set_io",
    "set_llm",
    "set_tool",
    "record_status",
    "SPAN_KIND_AGENT",
    "SPAN_KIND_CHAIN",
    "SPAN_KIND_LLM",
    "SPAN_KIND_TOOL",
    "SPAN_KIND_RETRIEVER",
    "SPAN_KIND_GUARDRAIL",
]
