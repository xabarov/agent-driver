"""Emit product-shaped Phoenix/OpenInference trace smokes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

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
from agent_driver.runtime.phoenix_gate import build_phoenix_trace_gate
from agent_driver.runtime.validation import build_validation_gate_summary
from agent_driver.runtime.validation_artifacts import write_validation_artifacts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("excel", "chat-demo"), required=True)
    parser.add_argument("--endpoint", default="http://127.0.0.1:6006")
    parser.add_argument("--project-name", default=None)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    spec = _profile_spec(args.profile)
    project_name = args.project_name or spec["project_name"]
    output_dir = Path(
        args.output_dir
        or f".agent-driver/policy-supervision/{args.profile}-trace-smoke"
    )
    health = _health(args.endpoint)
    status = setup_phoenix_tracing(
        PhoenixTracingConfig(
            enabled=True,
            project_name=project_name,
            collector_endpoint=args.endpoint,
            batch=False,
        )
    )
    trace_id, span_count = _emit_profile_trace(spec, project_name, args.endpoint)
    flushed = flush_phoenix_tracing()

    reason = None
    if not health["ok"]:
        reason = "phoenix_http_unreachable"
    elif status.get("error"):
        reason = "phoenix_tracing_setup_failed"
    gate = build_phoenix_trace_gate(
        live_claim=True,
        span_count=span_count if trace_id else 0,
        trace_ids=[trace_id] if trace_id else None,
        endpoint=args.endpoint,
        project_name=project_name,
        reason=reason,
    )
    smoke_payload = {
        "profile": args.profile,
        "project_name": project_name,
        "endpoint": args.endpoint,
        "trace_id": trace_id,
        "span_count": span_count if trace_id else 0,
        "flushed": flushed,
        "gate": gate.model_dump(mode="json"),
        "expected_text": spec["expected_text"],
        "redaction": {"safe_by_default": True},
    }
    validation_gates = build_validation_gate_summary(
        {"validation_gates": [gate.model_dump(mode="json")]}
    )
    write_validation_artifacts(
        output_dir,
        validation_gates=validation_gates,
        phoenix_run_ids=[trace_id] if trace_id else None,
        extra_json_artifacts={"product_trace_smoke": smoke_payload},
    )
    print(
        json.dumps(
            {
                "profile": args.profile,
                "project_name": project_name,
                "trace_id_present": bool(trace_id),
                "span_count": span_count if trace_id else 0,
                "gate_status": gate.status,
                "output_dir": str(output_dir),
            },
            ensure_ascii=True,
        )
    )
    return 0 if health["ok"] and trace_id and not status.get("error") else 1


def _profile_spec(profile: str) -> dict[str, Any]:
    if profile == "excel":
        return {
            "project_name": "excel-ai",
            "root": "excel_ai.workbook_analysis",
            "guardrail": "policy.workbook_context_required",
            "subagent": "excel_ai.sheet_analysis",
            "llm_plan": "llm.workbook_plan",
            "tool": "tool.excel_read_table",
            "llm_final": "llm.workbook_answer",
            "tool_name": "excel_read_table",
            "tool_args": {"sheet": "Revenue", "range": "A1:D12"},
            "tool_result": {"rows": 12, "columns": 4, "redacted": True},
            "input": "Analyze revenue workbook risk using visible sheet evidence.",
            "output": "Workbook evidence was inspected and summarized.",
            "expected_text": [
                "excel_ai.workbook_analysis",
                "policy.workbook_context_required",
                "excel_ai.sheet_analysis",
                "llm.workbook_plan",
                "tool.excel_read_table",
                "llm.workbook_answer",
            ],
        }
    return {
        "project_name": "agent-driver-chat-demo",
        "root": "chat_demo.deep_research",
        "guardrail": "policy.required_source_evidence",
        "subagent": "chat_demo.researcher",
        "llm_plan": "llm.research_plan",
        "tool": "tool.web_fetch",
        "llm_final": "llm.report_answer",
        "tool_name": "web_fetch",
        "tool_args": {"url": "https://example.com/source", "purpose": "evidence"},
        "tool_result": {"source_ids": ["source-demo-1"], "redacted": True},
        "input": "Research one source and summarize the supported answer.",
        "output": "A sourced research answer was drafted.",
        "expected_text": [
            "chat_demo.deep_research",
            "policy.required_source_evidence",
            "chat_demo.researcher",
            "llm.research_plan",
            "tool.web_fetch",
            "llm.report_answer",
        ],
    }


def _emit_profile_trace(
    spec: dict[str, Any],
    project_name: str,
    endpoint: str,
) -> tuple[str | None, int]:
    trace_id = None
    span_count = 0
    with oi_span(
        spec["root"],
        kind=SPAN_KIND_AGENT,
        attributes={
            "agent.project_name": project_name,
            "agent.run_id": f"{spec['root']}-smoke",
        },
    ) as root:
        trace_id = _span_trace_id(root)
        span_count += 1
        set_io(root, input=spec["input"], output={"ok": True, "endpoint": endpoint})
        with oi_span(
            spec["guardrail"],
            kind=SPAN_KIND_GUARDRAIL,
            attributes={"policy.id": spec["guardrail"], "policy.action": "observe"},
        ) as guardrail:
            span_count += 1
            set_io(guardrail, input={"policy": spec["guardrail"]}, output="observed")
            record_status(guardrail, ok=True)
        with oi_span(spec["subagent"], kind=SPAN_KIND_AGENT) as subagent:
            span_count += 1
            set_io(subagent, input=spec["input"], output=spec["output"])
            with oi_span(spec["llm_plan"], kind=SPAN_KIND_LLM) as llm:
                span_count += 1
                set_llm(
                    llm,
                    model="product-smoke-model",
                    provider="openrouter",
                    input_messages=[{"role": "user", "content": spec["input"]}],
                    output_messages=[
                        {"role": "assistant", "content": "Use one product tool."}
                    ],
                    prompt_tokens=17,
                    completion_tokens=8,
                    total_tokens=25,
                    finish_reason="tool_calls",
                    total_cost=0.0,
                )
                set_io(llm, input=spec["input"], output="tool required")
                record_status(llm, ok=True)
            with oi_span(spec["tool"], kind=SPAN_KIND_TOOL) as tool:
                span_count += 1
                set_tool(
                    tool,
                    name=spec["tool_name"],
                    arguments=spec["tool_args"],
                    result=spec["tool_result"],
                    call_id=f"call_{spec['tool_name']}_smoke",
                )
                record_status(tool, ok=True)
            with oi_span(spec["llm_final"], kind=SPAN_KIND_LLM) as final:
                span_count += 1
                set_llm(
                    final,
                    model="product-smoke-model",
                    provider="openrouter",
                    input_messages=[{"role": "tool", "content": "redacted evidence"}],
                    output_messages=[{"role": "assistant", "content": spec["output"]}],
                    prompt_tokens=9,
                    completion_tokens=9,
                    total_tokens=18,
                    finish_reason="stop",
                    total_cost=0.0,
                )
                set_io(final, input="redacted evidence", output=spec["output"])
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
    except Exception as exc:  # noqa: BLE001 - smoke prints no secret data
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
