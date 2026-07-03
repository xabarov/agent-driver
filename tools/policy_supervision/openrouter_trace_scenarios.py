"""Run cheap live OpenRouter scenarios with Phoenix trace UX spans."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from agent_driver.contracts.messages import ChatMessage, ChatRole
from agent_driver.contracts.policy import ValidationGateResult
from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.openai_compatible import OpenAICompatibleProvider
from agent_driver.observability.openinference import (
    SPAN_KIND_AGENT,
    SPAN_KIND_GUARDRAIL,
    SPAN_KIND_LLM,
    oi_span,
    record_status,
    set_io,
    set_llm,
)
from agent_driver.observability.phoenix import (
    PhoenixTracingConfig,
    flush_phoenix_tracing,
    setup_phoenix_tracing,
)
from agent_driver.runtime.validation import build_validation_gate_summary
from agent_driver.runtime.validation_artifacts import write_validation_artifacts

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_DEFAULT_MODEL = "deepseek/deepseek-v4-flash"


async def main_async() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=".agent-driver/policy-supervision/openrouter-trace-scenarios")
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=_OPENROUTER_BASE_URL)
    parser.add_argument("--phoenix-endpoint", default="http://127.0.0.1:6006")
    parser.add_argument("--phoenix-project-name", default="agent-driver-policy-supervision")
    args = parser.parse_args()

    env = os.environ
    api_key = (
        env.get("OPENROUTER_API_KEY")
        or env.get("AGENT_DRIVER_API_KEY")
        or env.get("LLM_API_KEY")
    )
    model = args.model or env.get("AGENT_DRIVER_MODEL") or _DEFAULT_MODEL
    if not api_key:
        result = {
            "status": "skipped",
            "reason": "openrouter_api_key_missing",
            "scenarios": [],
            "trace_ids": [],
        }
        _write_artifacts(Path(args.output_dir), result, args.phoenix_endpoint, args.phoenix_project_name)
        print(json.dumps(result, ensure_ascii=True))
        return 0

    phoenix_status = setup_phoenix_tracing(
        PhoenixTracingConfig(
            enabled=True,
            project_name=args.phoenix_project_name,
            collector_endpoint=args.phoenix_endpoint,
            batch=False,
        )
    )
    provider = OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="openrouter",
            base_url=args.base_url,
            api_key=api_key,
            model=model,
            timeout_s=30.0,
        )
    )
    scenarios: list[dict[str, Any]] = []
    for spec in _scenario_specs():
        scenarios.append(
            await _run_scenario(
                provider=provider,
                model=model,
                base_url=args.base_url,
                spec=spec,
            )
        )
    flush_phoenix_tracing()
    trace_ids = [
        scenario["trace_id"]
        for scenario in scenarios
        if isinstance(scenario.get("trace_id"), str) and scenario["trace_id"]
    ]
    passed = bool(trace_ids) and all(
        scenario.get("status") == "passed" for scenario in scenarios
    )
    result = {
        "status": "passed" if passed else "failed",
        "reason": (
            "openrouter_trace_scenarios_passed"
            if passed
            else "openrouter_trace_scenarios_failed"
        ),
        "model": model,
        "phoenix_tracing": phoenix_status,
        "scenarios": scenarios,
        "trace_ids": trace_ids,
        "redaction": {"safe_by_default": True, "contains_api_key": False},
    }
    _write_artifacts(
        Path(args.output_dir),
        result,
        args.phoenix_endpoint,
        args.phoenix_project_name,
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "scenario_count": len(scenarios),
                "trace_count": len(trace_ids),
                "output_dir": args.output_dir,
            },
            ensure_ascii=True,
        )
    )
    return 0 if passed else 1


def _scenario_specs() -> list[dict[str, Any]]:
    return [
        {
            "id": "simple_completion",
            "prompt": "Return exactly: ok",
            "max_tokens": 8,
            "guardrail": None,
        },
        {
            "id": "request_shape_tool_path",
            "prompt": "Return a tiny JSON object with ok true.",
            "max_tokens": 16,
            "guardrail": {
                "policy.id": "provider_request_shape_preflight",
                "policy.action": "reshape_request",
            },
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "lookup",
                        "description": "Synthetic policy-supervision lookup.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            "tool_choice": "auto",
        },
        {
            "id": "guardrail_missing_evidence",
            "prompt": "Answer in one short sentence without citing sources.",
            "max_tokens": 24,
            "guardrail": {
                "policy.id": "required_source_evidence",
                "policy.action": "warn",
                "status": "error",
                "description": "required source evidence missing",
            },
        },
    ]


async def _run_scenario(
    *,
    provider: OpenAICompatibleProvider,
    model: str,
    base_url: str,
    spec: dict[str, Any],
) -> dict[str, Any]:
    request = LlmRequest(
        messages=[ChatMessage(role=ChatRole.USER, content=spec["prompt"])],
        max_tokens=spec.get("max_tokens"),
        tools=spec.get("tools") or [],
        tool_choice=spec.get("tool_choice"),
    )
    with oi_span(
        f"openrouter_scenario.{spec['id']}",
        kind=SPAN_KIND_AGENT,
        attributes={
            "agent.run_id": f"openrouter-{spec['id']}",
            "agent.provider": "openrouter",
        },
    ) as root:
        trace_id = _span_trace_id(root)
        set_io(root, input={"scenario": spec["id"]}, output={"model": model})
        guardrail = spec.get("guardrail")
        if isinstance(guardrail, dict):
            _record_guardrail(guardrail)
        try:
            response = await _complete_with_span(
                provider=provider,
                request=request,
                model=model,
                base_url=base_url,
                scenario_id=spec["id"],
            )
        except Exception as exc:  # noqa: BLE001 - live evidence must return status
            result = {
                "scenario_id": spec["id"],
                "status": "failed",
                "reason": f"{type(exc).__name__}: {exc}",
                "trace_id": trace_id,
            }
            set_io(root, output=result)
            record_status(root, ok=False, description=result["reason"], exception=exc)
            return result
        result = {
            "scenario_id": spec["id"],
            "status": "passed",
            "reason": "live_completion_passed",
            "trace_id": trace_id,
            "finish_reason": response.finish_reason.value,
            "usage": response.usage.model_dump(mode="json"),
        }
        set_io(root, output=result)
        record_status(root, ok=True)
        return result


def _record_guardrail(metadata: dict[str, Any]) -> None:
    with oi_span(
        str(metadata["policy.id"]),
        kind=SPAN_KIND_GUARDRAIL,
        attributes={
            "policy.id": metadata["policy.id"],
            "policy.action": metadata["policy.action"],
        },
    ) as span:
        set_io(
            span,
            input={"policy": metadata["policy.id"]},
            output={"action": metadata["policy.action"]},
        )
        is_error = metadata.get("status") == "error"
        record_status(
            span,
            ok=not is_error,
            description=str(metadata.get("description") or ""),
        )


async def _complete_with_span(
    *,
    provider: OpenAICompatibleProvider,
    request: LlmRequest,
    model: str,
    base_url: str,
    scenario_id: str,
) -> LlmResponse:
    with oi_span(f"llm.{scenario_id}", kind=SPAN_KIND_LLM) as span:
        set_llm(
            span,
            model=model,
            provider="openrouter",
            invocation_parameters={
                "base_url": base_url,
                "max_tokens": request.max_tokens,
                "tool_choice": request.tool_choice,
            },
            input_messages=[
                {"role": message.role.value, "content": message.content}
                for message in request.messages
            ],
        )
        set_io(span, input=[message.content for message in request.messages])
        response = await provider.complete(request)
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
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
            total_tokens=response.usage.total_tokens,
            finish_reason=response.finish_reason.value,
            total_cost=_cost_total(response),
        )
        set_io(span, output=response.message.content)
        record_status(span, ok=True)
        return response


def _cost_total(response: LlmResponse) -> float | None:
    value = response.metadata.get("cost_usd") or response.metadata.get("cost")
    return float(value) if isinstance(value, (int, float)) else None


def _write_artifacts(
    output_dir: Path,
    result: dict[str, Any],
    endpoint: str,
    project_name: str,
) -> None:
    gate = ValidationGateResult(
        gate_id="openrouter_trace_scenarios",
        status=result["status"],
        reason=result.get("reason"),
        redacted_metadata={
            "scenario_count": len(result.get("scenarios") or []),
            "trace_count": len(result.get("trace_ids") or []),
            "endpoint": endpoint,
            "project_name": project_name,
        },
    )
    validation_gates = build_validation_gate_summary(
        {"validation_gates": [gate.model_dump(mode="json")]}
    )
    write_validation_artifacts(
        output_dir,
        validation_gates=validation_gates,
        phoenix_run_ids=result.get("trace_ids") or None,
        extra_json_artifacts={"openrouter_trace_scenarios": result},
    )


def _span_trace_id(span: object) -> str | None:
    try:
        context = span.get_span_context()  # type: ignore[attr-defined]
        trace_id = int(context.trace_id)
    except Exception:
        return None
    if trace_id <= 0:
        return None
    return f"{trace_id:032x}"


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
