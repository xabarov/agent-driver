"""Emit a Phoenix/OpenInference hierarchy and write gate artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx

from agent_driver.observability.openinference import (
    SPAN_KIND_AGENT,
    SPAN_KIND_GUARDRAIL,
    SPAN_KIND_LLM,
    SPAN_KIND_TOOL,
    oi_span,
    record_status,
    set_io,
    set_llm,
    set_tool,
)
from agent_driver.observability.phoenix import (
    PhoenixTracingConfig,
    flush_phoenix_tracing,
    setup_phoenix_tracing,
)
from agent_driver.runtime.phoenix_gate import write_phoenix_gate_artifacts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="http://127.0.0.1:6006")
    parser.add_argument("--project-name", default="agent-driver-policy-supervision")
    parser.add_argument(
        "--output-dir",
        default=".agent-driver/policy-supervision/phoenix-smoke",
    )
    args = parser.parse_args()

    health = _health(args.endpoint)
    status = setup_phoenix_tracing(
        PhoenixTracingConfig(
            enabled=True,
            project_name=args.project_name,
            collector_endpoint=args.endpoint,
            batch=False,
        )
    )
    trace_id, emitted_span_count = _emit_trace_hierarchy(args.project_name, args.endpoint)
    flushed = flush_phoenix_tracing()

    setup_error = status.get("error") if isinstance(status, dict) else None
    reason = None
    if not health["ok"]:
        reason = "phoenix_http_unreachable"
    elif setup_error:
        reason = "phoenix_tracing_setup_failed"
    result = write_phoenix_gate_artifacts(
        Path(args.output_dir),
        live_claim=True,
        span_count=emitted_span_count if trace_id else 0,
        trace_ids=[trace_id] if trace_id else None,
        endpoint=args.endpoint,
        project_name=args.project_name,
        reason=reason,
    )
    print(
        json.dumps(
            {
                "health": health,
                "tracing_enabled": bool(status.get("enabled")),
                "trace_id_present": bool(trace_id),
                "span_count": emitted_span_count if trace_id else 0,
                "flushed": flushed,
                "gate_status": result["gate"]["status"],
                "output_dir": str(args.output_dir),
            },
            ensure_ascii=True,
        )
    )
    return 0 if health["ok"] and trace_id and not setup_error else 1


def _emit_trace_hierarchy(project_name: str, endpoint: str) -> tuple[str | None, int]:
    trace_id = None
    span_count = 0
    with oi_span(
        "policy_supervision.agent_run",
        kind=SPAN_KIND_AGENT,
        attributes={
            "agent.validation.gate_id": "phoenix_trace",
            "agent.run_id": "phoenix-smoke",
            "agent.project_name": project_name,
        },
    ) as root:
        trace_id = _span_trace_id(root)
        span_count += 1
        set_io(
            root,
            input={"scenario": "phoenix_trace_shape_smoke"},
            output={"ok": True, "endpoint": endpoint},
        )
        with oi_span(
            "policy.required_source_evidence",
            kind=SPAN_KIND_GUARDRAIL,
            attributes={
                "policy.id": "required_source_evidence",
                "policy.action": "observe",
            },
        ) as guardrail:
            span_count += 1
            set_io(
                guardrail,
                input={"required": ["source_evidence"]},
                output={"status": "observed"},
            )
            record_status(guardrail, ok=True)
        with oi_span(
            "subagent.research",
            kind=SPAN_KIND_AGENT,
            attributes={"agent.subagent_id": "research-smoke"},
        ) as subagent:
            span_count += 1
            set_io(
                subagent,
                input="Find one source and produce a concise answer.",
                output="Source evidence collected.",
            )
            with oi_span("llm.plan", kind=SPAN_KIND_LLM) as llm:
                span_count += 1
                set_llm(
                    llm,
                    model="smoke-model",
                    provider="openrouter",
                    invocation_parameters={"temperature": 0, "max_tokens": 32},
                    input_messages=[
                        {
                            "role": "user",
                            "content": "Plan a one-source evidence check.",
                        }
                    ],
                    output_messages=[
                        {
                            "role": "assistant",
                            "content": "Call the source lookup tool once.",
                        }
                    ],
                    prompt_tokens=11,
                    completion_tokens=9,
                    total_tokens=20,
                    finish_reason="tool_calls",
                    total_cost=0.0,
                )
                set_io(llm, input="Plan a one-source evidence check.", output="lookup")
                record_status(llm, ok=True)
            with oi_span("tool.source_lookup", kind=SPAN_KIND_TOOL) as tool:
                span_count += 1
                set_tool(
                    tool,
                    name="source_lookup",
                    description="Synthetic smoke source lookup.",
                    arguments={"query": "phoenix trace smoke", "limit": 1},
                    result={
                        "source_ids": ["source-smoke-1"],
                        "content_preview": "redacted synthetic source",
                    },
                    call_id="call_source_lookup_smoke",
                )
                record_status(tool, ok=True)
            with oi_span("llm.final", kind=SPAN_KIND_LLM) as final:
                span_count += 1
                set_llm(
                    final,
                    model="smoke-model",
                    provider="openrouter",
                    input_messages=[
                        {
                            "role": "tool",
                            "content": "source-smoke-1 confirms the smoke.",
                        }
                    ],
                    output_messages=[
                        {
                            "role": "assistant",
                            "content": "The Phoenix trace smoke is complete.",
                        }
                    ],
                    prompt_tokens=8,
                    completion_tokens=7,
                    total_tokens=15,
                    finish_reason="stop",
                    total_cost=0.0,
                )
                set_io(final, input="source-smoke-1", output="smoke complete")
                record_status(final, ok=True)
            record_status(subagent, ok=True)
        record_status(root, ok=True)
    return trace_id, span_count


def _health(endpoint: str) -> dict[str, object]:
    base = endpoint.rstrip("/")
    if base.endswith("/v1/traces"):
        base = base.removesuffix("/v1/traces")
    try:
        response = httpx.get(f"{base}/healthz", timeout=5.0)
        return {"ok": response.is_success, "status_code": response.status_code}
    except Exception as exc:  # noqa: BLE001 - smoke prints diagnostic, no secret data
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _span_trace_id(span: object) -> str | None:
    try:
        context = span.get_span_context()  # type: ignore[attr-defined]
        trace_id = int(context.trace_id)
    except Exception:
        return None
    if trace_id <= 0:
        return None
    return f"{trace_id:032x}"


if __name__ == "__main__":
    raise SystemExit(main())
