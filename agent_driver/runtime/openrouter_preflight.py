"""Cheap OpenRouter preflight ladder for policy-supervision gates."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent_driver.contracts.messages import ChatMessage, ChatRole
from agent_driver.contracts.policy import ValidationGateResult
from agent_driver.llm.contracts import LlmRequest
from agent_driver.llm.provider_capabilities import resolve_openai_compatible_capabilities
from agent_driver.llm.provider_route_profiles import (
    build_provider_request_shape_plan,
    preview_provider_preflight,
    resolve_openai_compatible_route_profile,
)
from agent_driver.llm.providers_impl.openai_compatible import OpenAICompatibleProvider
from agent_driver.observability.openinference import (
    SPAN_KIND_LLM,
    oi_span,
    record_status,
    set_io,
    set_llm,
)
from agent_driver.observability.phoenix import (
    PhoenixTracingConfig,
    setup_phoenix_tracing,
)
from agent_driver.runtime.phoenix_gate import write_phoenix_gate_artifacts
from agent_driver.runtime.validation import build_validation_gate_summary
from agent_driver.runtime.validation_artifacts import write_validation_artifacts

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_DEFAULT_MODEL = "qwen/qwen-2.5-7b-instruct"


async def run_openrouter_preflight_ladder(
    *,
    output_dir: str | Path,
    env: Mapping[str, str] | None = None,
    live: bool = False,
    model: str | None = None,
    base_url: str = _OPENROUTER_BASE_URL,
    phoenix_endpoint: str | None = None,
    phoenix_project_name: str = "agent-driver-policy-supervision",
    phoenix_gate_output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Run deterministic and optional live OpenRouter preflight steps.

    The deterministic step previews request-shaping without network access. The
    live step only runs when ``live=True`` and an OpenRouter-compatible key is
    configured; otherwise it is recorded as ``skipped``.
    """

    selected_env = dict(os.environ if env is None else env)
    selected_model = model or selected_env.get("AGENT_DRIVER_MODEL") or _DEFAULT_MODEL
    api_key = (
        selected_env.get("OPENROUTER_API_KEY")
        or selected_env.get("AGENT_DRIVER_API_KEY")
        or selected_env.get("LLM_API_KEY")
    )
    request = _probe_request()
    capability = resolve_openai_compatible_capabilities(
        provider_name="openrouter",
        base_url=base_url,
        model=selected_model,
    )
    route_profile = resolve_openai_compatible_route_profile(
        provider_name="openrouter",
        base_url=base_url,
        model=selected_model,
        capability_profile=capability,
    )
    preflight = preview_provider_preflight(
        provider_name="openrouter",
        provider_kind="openai_compatible",
        model=selected_model,
        route_profile=route_profile,
        capability_profile=capability,
        request=request,
    )
    plan = build_provider_request_shape_plan(
        preflight=preflight,
        request=request,
        enforce=True,
    )

    live_result = await _optional_live_probe(
        api_key=api_key,
        base_url=base_url,
        model=selected_model,
        request=plan.reshaped_request or request,
        live=live,
        phoenix_endpoint=phoenix_endpoint,
        phoenix_project_name=phoenix_project_name,
    )
    gate = ValidationGateResult(
        gate_id="openrouter_live_preflight",
        status=live_result["status"],
        reason=live_result.get("reason"),
        redacted_metadata={
            "model": selected_model,
            "deterministic_status": preflight.status,
            "selected_action": plan.selected_action,
            "live_requested": live,
            "api_key_configured": bool(api_key),
        },
    )
    validation_gates = build_validation_gate_summary(
        {"validation_gates": [gate.model_dump(mode="json")]}
    )
    payload = {
        "provider": "openrouter",
        "model": selected_model,
        "route_profile": route_profile.to_metadata(),
        "provider_preflight": preflight.to_metadata(),
        "request_shape_plan": plan.to_metadata(),
        "live_result": live_result,
        "validation_gates": validation_gates,
        "redaction": {
            "safe_by_default": True,
            "contains_api_key": False,
            "contains_raw_response": False,
        },
    }
    manifest = write_validation_artifacts(
        output_dir,
        validation_gates=validation_gates,
        benchmark_json={"openrouter_preflight_ladder": payload},
    )
    phoenix_gate = None
    if phoenix_gate_output_dir is not None:
        trace_id = live_result.get("phoenix_trace_id")
        span_count = 1 if isinstance(trace_id, str) and trace_id else 0
        phoenix_reason = live_result.get("phoenix_reason")
        if live and live_result.get("phoenix_setup_error"):
            phoenix_reason = "phoenix_tracing_setup_failed"
        phoenix_gate = write_phoenix_gate_artifacts(
            phoenix_gate_output_dir,
            live_claim=live,
            span_count=span_count if live else None,
            trace_ids=[trace_id] if isinstance(trace_id, str) and trace_id else None,
            endpoint=phoenix_endpoint,
            project_name=phoenix_project_name,
            reason=phoenix_reason if isinstance(phoenix_reason, str) else None,
        )
    return {**payload, "artifact_manifest": manifest, "phoenix_gate": phoenix_gate}


def _probe_request() -> LlmRequest:
    return LlmRequest(
        messages=[
            ChatMessage(
                role=ChatRole.USER,
                content="Return a minimal JSON object with ok=true.",
            )
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "Tiny deterministic probe tool.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        tool_choice={"type": "tool", "name": "lookup"},
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "preflight",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"],
                    "additionalProperties": False,
                },
            },
        },
        max_tokens=16,
    )


async def _optional_live_probe(
    *,
    api_key: str | None,
    base_url: str,
    model: str,
    request: LlmRequest,
    live: bool,
    phoenix_endpoint: str | None = None,
    phoenix_project_name: str = "agent-driver-policy-supervision",
) -> dict[str, Any]:
    if not live:
        return {"status": "skipped", "reason": "live_preflight_not_requested"}
    if not api_key:
        return {"status": "skipped", "reason": "openrouter_api_key_missing"}
    provider = OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="openrouter",
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_s=30.0,
        )
    )
    phoenix_status: dict[str, Any] | None = None
    if phoenix_endpoint:
        phoenix_status = setup_phoenix_tracing(
            PhoenixTracingConfig(
                enabled=True,
                project_name=phoenix_project_name,
                collector_endpoint=phoenix_endpoint,
                batch=False,
            )
        )
    with oi_span(
        "policy_supervision.openrouter_live_preflight",
        kind=SPAN_KIND_LLM,
        attributes={
            "agent.validation.gate_id": "openrouter_live_preflight",
            "agent.provider": "openrouter",
        },
    ) as span:
        trace_id = _span_trace_id(span)
        set_io(span, input={"model": model, "messages": _message_preview(request)})
        set_llm(
            span,
            model=model,
            provider="openrouter",
            invocation_parameters={"base_url": base_url, "max_tokens": request.max_tokens},
            input_messages=[
                {"role": message.role.value, "content": message.content}
                for message in request.messages
            ],
        )
        status = await provider.healthcheck()
        if not status.healthy:
            result: dict[str, Any] = {
                "status": "failed",
                "reason": "openrouter_healthcheck_failed",
                "latency_ms": status.latency_ms,
            }
            _attach_phoenix_result(result, trace_id, phoenix_status)
            set_io(span, output=result)
            record_status(span, ok=False, description=result["reason"])
            return result
        try:
            response = await provider.complete(request)
        except Exception as exc:  # noqa: BLE001 - live gate should return evidence
            result = {
                "status": "failed",
                "reason": f"{type(exc).__name__}: {exc}",
                "latency_ms": status.latency_ms,
            }
            _attach_phoenix_result(result, trace_id, phoenix_status)
            set_io(span, output=result)
            record_status(span, ok=False, description=result["reason"], exception=exc)
            return result
        usage = response.usage.model_dump(mode="json") if response.usage else None
        result = {
            "status": "passed",
            "reason": "openrouter_live_completion_passed",
            "latency_ms": response.metadata.get("latency_ms") or status.latency_ms,
            "provider_request_id_present": bool(
                response.metadata.get("provider_request_id")
            ),
            "usage": usage,
        }
        _attach_phoenix_result(result, trace_id, phoenix_status)
        set_io(span, output=result)
        set_llm(
            span,
            model=model,
            provider="openrouter",
            output_messages=[
                {
                    "role": response.message.role.value,
                    "content": response.message.content,
                }
            ],
            prompt_tokens=response.usage.input_tokens if response.usage else None,
            completion_tokens=response.usage.output_tokens if response.usage else None,
            total_tokens=response.usage.total_tokens if response.usage else None,
        )
        record_status(span, ok=True)
        return result


def _message_preview(request: LlmRequest) -> list[dict[str, str]]:
    return [
        {"role": message.role.value, "content": message.content[:200]}
        for message in request.messages
    ]


def _span_trace_id(span: object) -> str | None:
    try:
        context = span.get_span_context()  # type: ignore[attr-defined]
        trace_id = int(context.trace_id)
    except Exception:
        return None
    if trace_id <= 0:
        return None
    return f"{trace_id:032x}"


def _attach_phoenix_result(
    result: dict[str, Any],
    trace_id: str | None,
    phoenix_status: dict[str, Any] | None,
) -> None:
    if trace_id:
        result["phoenix_trace_id"] = trace_id
    if phoenix_status:
        result["phoenix_tracing"] = phoenix_status
        error = phoenix_status.get("error")
        if isinstance(error, str) and error:
            result["phoenix_setup_error"] = error


__all__ = ["run_openrouter_preflight_ladder"]
