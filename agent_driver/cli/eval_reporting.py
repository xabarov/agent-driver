"""Human-readable eval rendering and run-artifact writers."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_driver.contracts.runtime import AgentRunOutput

from .eval_scenarios import EvalScenario

if TYPE_CHECKING:
    from agent_driver.cli.evals import EvalSummary

_REDACT_KEYS = {"api_key", "authorization", "token", "password", "secret", "bearer"}


def render_eval_inspect(summary: EvalSummary) -> str:
    """Render deterministic compact trace summary."""
    return "\n".join(
        [
            f"scenario> {summary.scenario_id}",
            f"run> {summary.run_id}",
            f"status> {summary.status} terminal_reason={summary.terminal_reason}",
            (
                "steps> "
                f"total={summary.steps_total} llm_calls={summary.llm_calls} "
                f"tool_calls={summary.tool_calls} elapsed_ms={summary.elapsed_ms} "
                f"runtime_step_count={summary.runtime_step_count}"
            ),
            (
                "tools> "
                f"repeated={summary.repeated_tools} "
                f"repeated_args={summary.repeated_tool_arguments} "
                f"by_status={summary.tools_by_status}"
            ),
            (
                "quality> "
                f"answer={summary.answer_relevance} tools={summary.tool_use_correctness} "
                f"efficiency={summary.efficiency}"
            ),
            f"bugs> {summary.bug_tags}",
        ]
    )


def render_eval_timeline(artifact_payload: dict[str, Any]) -> str:
    """Render compact deterministic timeline from per-scenario artifact JSON."""
    scenario = artifact_payload.get("scenario", {})
    summary = artifact_payload.get("summary", {})
    event_replay = artifact_payload.get("event_replay", [])
    tool_trace = artifact_payload.get("tool_trace", [])
    rows = [
        f"scenario> {scenario.get('scenario_id')}",
        f"status> {summary.get('status')} terminal_reason={summary.get('terminal_reason')}",
    ]
    for event in event_replay:
        if not isinstance(event, dict):
            continue
        rows.append(f"event> seq={event.get('seq')} type={event.get('type')}")
    for row in tool_trace:
        if not isinstance(row, dict):
            continue
        rows.append(
            f"tool> {row.get('tool_name')} status={row.get('status')} call_id={row.get('tool_call_id')}"
        )
    terminal = artifact_payload.get("terminal", {})
    final_answer = str(artifact_payload.get("final_answer", ""))
    rows.append(
        f"terminal> status={terminal.get('status')} reason={terminal.get('reason')}"
    )
    rows.append(f"final_answer_len> {len(final_answer)}")
    return "\n".join(rows)


def _write_run_artifact(
    *,
    target_dir: Path,
    output: AgentRunOutput,
    summary: EvalSummary,
    scenario: EvalScenario,
    rendered_prompt: str,
    sandbox_dir: Path | None,
) -> None:
    payload = {
        "scenario": {
            "scenario_id": scenario.scenario_id,
            "prompt": rendered_prompt,
            "prompt_template": scenario.prompt_template,
            "expected_tools": list(scenario.expected_tools),
            "forbidden_tools": list(scenario.forbidden_tools),
            "expected_answer_contains": list(scenario.expected_answer_contains),
            "expected_answer_any_of": [
                list(group) for group in scenario.expected_answer_any_of
            ],
            "follow_up_prompts": list(scenario.follow_up_prompts),
            "max_steps": scenario.max_steps,
            "max_tool_calls": scenario.max_tool_calls,
            "deadline_seconds": scenario.deadline_seconds,
            "tags": list(scenario.tags),
            "expected_min_tool_calls": scenario.expected_min_tool_calls,
            "expected_tool_chain_contains": list(scenario.expected_tool_chain_contains),
            "sandbox_required": scenario.sandbox_required,
            "sandbox_dir": str(sandbox_dir) if sandbox_dir is not None else None,
            "tool_packs": list(scenario.tool_packs),
            "allow_dangerous_tools": scenario.allow_dangerous_tools,
            "required_tools": list(scenario.required_tools),
        },
        "summary": asdict(summary),
        "run_output": _redact_secrets(output.model_dump(mode="json")),
        "event_replay": [
            {"seq": event.seq, "type": event.type.value, "created_at": event.created_at}
            for event in output.events
        ],
        "tool_trace": [row.model_dump(mode="json") for row in output.tool_trace],
        "final_answer": output.answer or "",
        "terminal": {
            "status": output.status.value,
            "reason": output.terminal_reason.value if output.terminal_reason else None,
        },
    }
    (target_dir / f"{scenario.scenario_id}.json").write_text(
        json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8"
    )


def _write_scorecard(
    *, target_dir: Path, summaries: list[EvalSummary], scenarios: list[EvalScenario]
) -> None:
    rows = ["# CLI Live Eval Report", ""]
    rows.append(f"Scenarios: {len(scenarios)}")
    rows.append("")
    for item in summaries:
        rows.append(f"## {item.scenario_id}")
        rows.append(f"- run_id: `{item.run_id}`")
        rows.append(
            f"- status: `{item.status}` terminal_reason=`{item.terminal_reason}`"
        )
        rows.append(
            f"- steps_total: `{item.steps_total}` llm_calls=`{item.llm_calls}` tool_calls=`{item.tool_calls}`"
        )
        rows.append(
            "- tool_chain: `"
            + (" -> ".join(item.actual_tool_chain) if item.actual_tool_chain else "-")
            + "`"
        )
        rows.append(
            "- tokens: "
            f"input=`{item.llm_usage.get('input_tokens', 0)}`, "
            f"output=`{item.llm_usage.get('output_tokens', 0)}`, "
            f"total=`{item.llm_usage.get('total_tokens', 0)}`, "
            f"after_report=`{item.research_efficiency.get('output_tokens_after_first_report_update', 0)}`"
        )
        rows.append(
            "- research_efficiency: "
            f"artifact_expected=`{item.research_efficiency.get('deep_research_artifact_expected', False)}`, "
            f"report_updates=`{item.research_efficiency.get('report_update_count', 0)}`, "
            f"first_tool=`{item.research_efficiency.get('first_tool') or '-'}`"
        )
        rows.append(
            f"- repeated_tools: `{', '.join(item.repeated_tools) if item.repeated_tools else '-'}`"
        )
        rows.append(f"- repeated_tool_arguments: `{len(item.repeated_tool_arguments)}`")
        rows.append(f"- empty_tool_results: `{item.empty_tool_results}`")
        rows.append(
            f"- quality: answer=`{item.answer_relevance}`, tools=`{item.tool_use_correctness}`, efficiency=`{item.efficiency}`"
        )
        rows.append(f"- bug_tags: `{', '.join(item.bug_tags)}`")
        rows.append(f"- notes: {item.notes}")
        rows.append("")
    (target_dir / "report.md").write_text("\n".join(rows), encoding="utf-8")
    (target_dir / "summary.json").write_text(
        json.dumps([asdict(item) for item in summaries], ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def _write_triage(*, target_dir: Path, summaries: list[EvalSummary]) -> None:
    grouped: dict[str, list[str]] = {}
    for row in summaries:
        for tag in row.bug_tags:
            grouped.setdefault(tag, []).append(row.scenario_id)
    (target_dir / "triage.json").write_text(
        json.dumps(grouped, ensure_ascii=True, indent=2), encoding="utf-8"
    )


def _redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if key.lower() in _REDACT_KEYS:
                redacted[key] = "***REDACTED***"
            else:
                redacted[key] = _redact_secrets(item)
        return redacted
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    if isinstance(value, str):
        lowered = value.lower()
        if lowered.startswith("sk-") or "api_key" in lowered or "bearer " in lowered:
            return "***REDACTED***"
        return value
    return value
