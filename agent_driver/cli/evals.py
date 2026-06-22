"""Offline-first CLI evaluation harness: run scenarios, score, report."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from agent_driver.cli.providers import CliProviderConfig, build_cli_provider
from agent_driver.cli.tools import CliToolConfig, build_cli_toolset
from agent_driver.contracts import ResumeAction, ToolRisk
from agent_driver.contracts.events import RuntimeEvent
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.runtime import AgentRunInput, AgentRunOutput
from agent_driver.observability.run_trace.summary import summarize_run_trace
from agent_driver.runtime import RuntimeStoreFactoryConfig, create_runtime_store_bundle
from agent_driver.runtime.single_agent.config_sections import PythonToolSettings
from agent_driver.runtime.single_agent.types import RunnerConfig
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet
from agent_driver.tools.builtin.python_imports import resolve_python_default_imports

from .eval_providers import (
    _EvalGammaScipyFakeProvider,
    _EvalGammaStdlibFakeProvider,
    _EvalInterruptFakeProvider,
    _EvalPandasLinalgFakeProvider,
)
from .eval_reporting import (
    _write_run_artifact,
    _write_scorecard,
    _write_triage,
    render_eval_inspect,
    render_eval_timeline,
)
from .eval_scenarios import (
    EvalScenario,
    assert_eval_scenario_tool_packs_are_tuples,
    default_deep_scenarios,
    default_live_scenarios,
    default_regression_scenarios,
    default_smoke_scenarios,
    live_scenarios_for_suite,
)
from .eval_scoring import (
    _answer_matches_expectations,
    _detect_answer_language,
    _forbidden_python_imports_after_first_python,
    _is_subsequence,
    classify_bug_tags,
)

_LIVE_OPT_IN_ENV = "AGENT_DRIVER_RUN_LIVE_CLI_EVALS"


class LiveEvalSkipped(RuntimeError):
    """Raised when live eval should be skipped with explanation."""


def is_live_eval_enabled(*, offline: bool) -> bool:
    """Return whether live eval run is enabled."""
    if offline:
        return True
    import os

    return os.environ.get(_LIVE_OPT_IN_ENV) == "1"


def can_run_provider(config: CliProviderConfig) -> tuple[bool, str | None]:
    """Return whether provider config appears runnable for live eval."""
    provider = config.provider
    if provider == "fake":
        return True, None
    import os

    env = os.environ
    if provider in {"openrouter", "vllm"}:
        has_base = bool(config.base_url or env.get("AGENT_DRIVER_BASE_URL"))
        has_model = bool(config.model or env.get("AGENT_DRIVER_MODEL"))
        has_key = bool(config.api_key or env.get("AGENT_DRIVER_API_KEY"))
        if has_base and has_model and has_key:
            return True, None
        return (
            False,
            f"{provider} provider is not fully configured (base_url/model/api_key)",
        )
    if provider == "ollama":
        has_model = bool(config.model or env.get("AGENT_DRIVER_MODEL"))
        if has_model:
            return True, None
        return False, "ollama provider requires model (flag or AGENT_DRIVER_MODEL)"
    return False, f"unsupported provider {provider}"


@dataclass(frozen=True, slots=True)
class EvalSummary:
    """Structured summary for one run."""

    scenario_id: str
    run_id: str
    status: str
    terminal_reason: str | None
    steps_total: int
    llm_calls: int
    tool_calls: int
    tools_by_status: dict[str, int]
    tools_by_name_status: dict[str, dict[str, int]]
    repeated_tools: list[str]
    repeated_tool_arguments: list[str]
    empty_tool_results: int
    interrupts_or_denials: int
    answer_length: int
    answer_language: str
    elapsed_ms: int
    expected_tools_missing: list[str]
    forbidden_tools_used: list[str]
    answer_relevance: str
    tool_use_correctness: str
    efficiency: str
    notes: str
    bug_tags: list[str]
    actual_tool_chain: list[str] = field(default_factory=list)
    expected_chain_satisfied: bool = True
    min_tool_calls_satisfied: bool = True
    required_tools_missing: list[str] = field(default_factory=list)
    runtime_step_count: int | None = None
    llm_usage: dict[str, Any] = field(default_factory=dict)
    research_efficiency: dict[str, Any] = field(default_factory=dict)


_TRANSIENT_EVAL_ERROR_MARKERS = (
    "llm completion failed",
    "readtimeout",
    "read timeout",
    "timed out",
    "timeout",
    "connection reset",
    "connection error",
)


def _is_transient_eval_error(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    message = str(exc).lower()
    return any(marker in message for marker in _TRANSIENT_EVAL_ERROR_MARKERS)


async def _run_eval_scenario_with_retry(
    *,
    scenario: EvalScenario,
    agent_resolver: Any,
    sandbox_root: Path,
    max_attempts: int = 2,
) -> tuple[AgentRunOutput, EvalSummary, list[str], Path | None]:
    """Run one scenario, retrying once on transient provider/network failures."""
    last_exc: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            return await _run_eval_scenario(
                scenario=scenario,
                agent_resolver=agent_resolver,
                sandbox_root=sandbox_root,
            )
        except Exception as exc:
            last_exc = exc
            if attempt + 1 >= max_attempts or not _is_transient_eval_error(exc):
                raise
            await asyncio.sleep(min(4.0, 1.5 * (attempt + 1)))
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("unreachable eval retry loop")


async def _run_eval_scenario(
    *,
    scenario: EvalScenario,
    agent_resolver: Any,
    sandbox_root: Path,
) -> tuple[AgentRunOutput, EvalSummary, list[str], Path | None]:
    """Execute one eval scenario (single- or multi-turn, optional interrupt resume)."""
    started = time.monotonic()
    base_run_id = (
        f"run_eval_{scenario.scenario_id}_{datetime.now(UTC).strftime('%H%M%S')}"
    )
    sandbox_dir: Path | None = None
    if scenario.sandbox_required:
        sandbox_dir = (sandbox_root / scenario.scenario_id).resolve()
        sandbox_dir.mkdir(parents=True, exist_ok=True)
    prompts = [scenario.prompt]
    if scenario.prompt_template:
        prompts[0] = scenario.prompt_template.format(
            sandbox=(str(sandbox_dir) if sandbox_dir is not None else ""),
            repo_root=str(Path.cwd().resolve()),
        )
    prompts.extend(scenario.follow_up_prompts)
    agent = agent_resolver(scenario)
    thread_id = f"thread_eval_{scenario.scenario_id}"
    if scenario.interrupt_resume:
        target_path = (
            (sandbox_dir / scenario.interrupt_resume_path).resolve()
            if sandbox_dir is not None
            else Path(scenario.interrupt_resume_path).resolve()
        )
        paused = await agent.run(
            AgentRunInput(
                input=prompts[0],
                run_id=base_run_id,
                thread_id=thread_id,
                agent_id="agent.cli.eval",
                graph_preset="single_react",
                stream=False,
                max_steps=scenario.max_steps,
                max_tool_calls=scenario.max_tool_calls,
                deadline_seconds=scenario.deadline_seconds,
                tool_policy={"approval_required_for_risk": ToolRisk.MEDIUM.value},
                app_metadata={
                    "eval_scenario_id": scenario.scenario_id,
                    "eval_sandbox_dir": (
                        str(sandbox_dir) if sandbox_dir is not None else None
                    ),
                    "workspace_cwd": str(
                        sandbox_dir if sandbox_dir is not None else Path.cwd().resolve()
                    ),
                },
            )
        )
        if paused.status.value != "paused" or paused.interrupt is None:
            raise RuntimeError(
                f"interrupt_resume scenario expected paused run, got {paused.status.value}"
            )
        output = await agent.resume(
            run_id=paused.run_id,
            interrupt_id=paused.interrupt.interrupt_id,
            action=ResumeAction.APPROVE,
        )
        outputs = [output]
    else:
        protocol_messages: list[ChatMessage] = []
        outputs = []
        for turn_index, prompt in enumerate(prompts):
            protocol_messages.append(ChatMessage(role="user", content=prompt))
            turn_max_steps = scenario.max_steps
            turn_max_tool_calls = scenario.max_tool_calls
            if turn_index > 0:
                if scenario.follow_up_max_steps is not None:
                    turn_max_steps = scenario.follow_up_max_steps
                if scenario.follow_up_max_tool_calls is not None:
                    turn_max_tool_calls = scenario.follow_up_max_tool_calls
            turn_output = await agent.run(
                AgentRunInput(
                    input=prompt,
                    run_id=f"{base_run_id}_t{turn_index}",
                    thread_id=thread_id,
                    messages=(
                        tuple(protocol_messages[:-1])
                        if len(protocol_messages) > 1
                        else ()
                    ),
                    agent_id="agent.cli.eval",
                    graph_preset="single_react",
                    stream=False,
                    max_steps=turn_max_steps,
                    max_tool_calls=turn_max_tool_calls,
                    deadline_seconds=scenario.deadline_seconds,
                    app_metadata={
                        "eval_scenario_id": scenario.scenario_id,
                        "eval_sandbox_dir": (
                            str(sandbox_dir) if sandbox_dir is not None else None
                        ),
                        "eval_expected_min_tool_calls": scenario.expected_min_tool_calls,
                        "workspace_cwd": str(
                            sandbox_dir
                            if sandbox_dir is not None
                            else Path.cwd().resolve()
                        ),
                        "eval_turn_index": turn_index,
                    },
                )
            )
            outputs.append(turn_output)
            if turn_output.answer:
                protocol_messages.append(
                    ChatMessage(role="assistant", content=turn_output.answer)
                )
        output = _merge_eval_outputs(outputs, base_run_id=base_run_id)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    summary = summarize_run(scenario=scenario, output=output, elapsed_ms=elapsed_ms)
    return output, summary, prompts, sandbox_dir


async def run_live_evaluation(
    *,
    provider_config: CliProviderConfig,
    tool_config: CliToolConfig,
    store_config: RuntimeStoreFactoryConfig,
    output_dir: Path,
    scenarios: list[EvalScenario] | None = None,
    offline: bool = False,
    continue_on_error: bool = False,
) -> tuple[Path, list[EvalSummary]]:
    """Run evaluation scenarios and persist artifacts."""
    if not is_live_eval_enabled(offline=offline):
        raise LiveEvalSkipped(
            f"live eval is disabled; set {_LIVE_OPT_IN_ENV}=1 or pass offline mode"
        )
    runnable, reason = can_run_provider(provider_config)
    if not runnable:
        raise LiveEvalSkipped(f"live eval skipped: {reason}")
    selected = list(scenarios or default_live_scenarios())
    assert_eval_scenario_tool_packs_are_tuples(selected)
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target_dir = (output_dir / timestamp).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    default_provider = build_cli_provider(provider_config)
    default_toolset = build_cli_toolset(tool_config)
    bundle = create_runtime_store_bundle(store_config)
    agent_cache: dict[tuple[str, ...], Any] = {}
    summaries: list[EvalSummary] = []
    failures: list[dict[str, str]] = []
    manifest = {
        "timestamp_utc": timestamp,
        "provider": provider_config.provider,
        "model": provider_config.model,
        "store_kind": store_config.kind,
        "scenarios": [scenario.scenario_id for scenario in selected],
        "continue_on_error": continue_on_error,
    }
    (target_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    sandbox_root = (target_dir / "sandbox").resolve()
    sandbox_root.mkdir(parents=True, exist_ok=True)

    def _agent_for_scenario(current: EvalScenario):
        scenario_provider = default_provider
        if provider_config.provider == "fake":
            if current.scenario_id == "python_gamma_stdlib_only":
                scenario_provider = _EvalGammaStdlibFakeProvider()
            elif current.scenario_id == "python_gamma_scipy":
                scenario_provider = _EvalGammaScipyFakeProvider()
            elif current.scenario_id == "python_pandas_linalg":
                scenario_provider = _EvalPandasLinalgFakeProvider()
        if current.interrupt_resume and provider_config.provider == "fake":
            target = (
                (
                    sandbox_root / current.scenario_id / current.interrupt_resume_path
                ).resolve()
                if current.sandbox_required
                else Path(current.interrupt_resume_path).resolve()
            )
            scenario_provider = _EvalInterruptFakeProvider(target_path=str(target))
        toolset: ToolSet = default_toolset
        enable_python = False
        if current.tool_packs:
            raw_packs = current.tool_packs
            if isinstance(raw_packs, str):
                raw_packs = (raw_packs,)
            normalized_packs = tuple(name.strip() for name in raw_packs if name.strip())
            enable_python = "python_exec" in normalized_packs
            toolset = build_cli_toolset(
                CliToolConfig(
                    tools_mode="none",
                    tool_packs=normalized_packs,
                    allow_dangerous_tools=current.allow_dangerous_tools,
                    enable_python=enable_python,
                )
            )
        include_scientific = current.scenario_id != "python_gamma_stdlib_only"
        key = (
            tuple(sorted(toolset.names or ())),
            enable_python,
            include_scientific,
            current.scenario_id,
        )
        cached = agent_cache.get(key)
        if cached is not None:
            return cached
        python_imports = resolve_python_default_imports(
            include_scientific=include_scientific
        )
        config = RunnerConfig(
            python_tool=PythonToolSettings(
                enabled=enable_python,
                include_scientific_stack=include_scientific,
                default_imports=python_imports,
            ),
        )
        created = create_agent(
            provider=scenario_provider,
            tools=toolset,
            config=config,
            checkpoint_store=bundle.checkpoint_store,
            event_log=bundle.event_log,
        )
        agent_cache[key] = created
        return created

    for scenario in selected:
        try:
            output, summary, prompts, sandbox_dir = await _run_eval_scenario_with_retry(
                scenario=scenario,
                agent_resolver=_agent_for_scenario,
                sandbox_root=sandbox_root,
            )
        except Exception as exc:
            if not continue_on_error:
                raise
            failures.append(
                {
                    "scenario_id": scenario.scenario_id,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                }
            )
            continue
        summaries.append(summary)
        _write_run_artifact(
            target_dir=target_dir,
            output=output,
            summary=summary,
            scenario=scenario,
            rendered_prompt="\n---\n".join(prompts),
            sandbox_dir=sandbox_dir,
        )
    if failures:
        (target_dir / "failures.json").write_text(
            json.dumps(failures, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
    if continue_on_error and failures and not summaries:
        raise RuntimeError("all scenarios failed; see failures.json in bundle dir")
    _write_scorecard(target_dir=target_dir, summaries=summaries, scenarios=selected)
    _write_triage(target_dir=target_dir, summaries=summaries)
    return target_dir, summaries


def _merge_eval_outputs(
    outputs: list[AgentRunOutput], *, base_run_id: str
) -> AgentRunOutput:
    """Merge multi-turn eval outputs into one summary envelope."""
    if not outputs:
        raise ValueError("outputs must not be empty")
    if len(outputs) == 1:
        return outputs[0]
    last = outputs[-1]
    merged_trace = [row for output in outputs for row in output.tool_trace]
    merged_events = [event for output in outputs for event in output.events]
    merged_metadata: dict[str, Any] = {}
    if isinstance(last.metadata, dict):
        merged_metadata = dict(last.metadata)
    tool_results: list[Any] = []
    for output in outputs:
        metadata = output.metadata if isinstance(output.metadata, dict) else {}
        rows = metadata.get("tool_results", [])
        if isinstance(rows, list):
            tool_results.extend(rows)
    merged_metadata["tool_results"] = tool_results
    merged_metadata["eval_turn_count"] = len(outputs)
    answers = [item.answer for item in outputs if item.answer]
    merged_answer = "\n---\n".join(answers) if answers else last.answer
    return last.model_copy(
        update={
            "run_id": base_run_id,
            "answer": merged_answer,
            "tool_trace": merged_trace,
            "events": merged_events,
            "metadata": merged_metadata,
        }
    )


def summarize_run(
    *, scenario: EvalScenario, output: AgentRunOutput, elapsed_ms: int
) -> EvalSummary:
    """Compute structured summary and quality score placeholders."""
    events = list(output.events)
    llm_calls = sum(1 for event in events if event.type.value == "llm_call_started")
    tool_trace = list(output.tool_trace)
    metadata = output.metadata if isinstance(output.metadata, dict) else {}
    tool_results = metadata.get("tool_results", [])

    tools_by_status: dict[str, int] = {}
    tools_by_name_status: dict[str, dict[str, int]] = {}
    tool_name_counts: dict[str, int] = {}
    tool_args_counts: dict[str, int] = {}
    interrupts_or_denials = 0

    for row in tool_trace:
        status = row.status.value
        tools_by_status[status] = tools_by_status.get(status, 0) + 1
        tool_name_counts[row.tool_name] = tool_name_counts.get(row.tool_name, 0) + 1
        status_by_name = tools_by_name_status.setdefault(row.tool_name, {})
        status_by_name[status] = status_by_name.get(status, 0) + 1
        if status in {"denied", "interrupted"}:
            interrupts_or_denials += 1

    if isinstance(tool_results, list):
        for item in tool_results:
            if not isinstance(item, dict):
                continue
            call = item.get("call")
            if not isinstance(call, dict):
                continue
            tool_name = str(call.get("tool_name") or "")
            args_payload: Any = call.get("args")
            args_key = f"{tool_name}:{json.dumps(args_payload, ensure_ascii=True, sort_keys=True)}"
            tool_args_counts[args_key] = tool_args_counts.get(args_key, 0) + 1

    if not tool_args_counts:
        for row in tool_trace:
            args_payload: Any = row.args_summary
            if not args_payload and isinstance(row.metadata, dict):
                args_payload = row.metadata.get("args", {})
            args_key = f"{row.tool_name}:{json.dumps(args_payload, ensure_ascii=True, sort_keys=True)}"
            tool_args_counts[args_key] = tool_args_counts.get(args_key, 0) + 1

    repeated_tools = sorted(
        name for name, count in tool_name_counts.items() if count > 1
    )
    repeated_tool_arguments = sorted(
        key for key, count in tool_args_counts.items() if count > 1
    )
    actual_tool_chain = [row.tool_name for row in tool_trace]
    chain_for_subsequence = actual_tool_chain
    if scenario.follow_up_prompts and scenario.expected_tool_chain_last_turn_only:
        pivot = max(1, len(actual_tool_chain) // 2)
        chain_for_subsequence = actual_tool_chain[pivot:]
    expected_chain_satisfied = _is_subsequence(
        expected=list(scenario.expected_tool_chain_contains),
        actual=chain_for_subsequence,
    )

    empty_tool_results = 0
    if isinstance(tool_results, list):
        for item in tool_results:
            if not isinstance(item, dict):
                continue
            structured = item.get("structured_output")
            if isinstance(structured, dict):
                rows = structured.get("results")
                if isinstance(rows, list) and not rows:
                    empty_tool_results += 1

    used_tools = {row.tool_name for row in tool_trace}
    tools_for_required = used_tools
    if scenario.follow_up_prompts and scenario.required_tools_last_turn_only:
        pivot = max(1, len(actual_tool_chain) // 2)
        tools_for_required = set(actual_tool_chain[pivot:])
    expected_missing = sorted(
        name for name in scenario.expected_tools if name not in used_tools
    )
    required_missing = sorted(
        name for name in scenario.required_tools if name not in tools_for_required
    )
    forbidden_used = sorted(
        name for name in scenario.forbidden_tools if name in used_tools
    )

    if forbidden_used or required_missing:
        tool_use_correctness = "fail"
    elif expected_missing:
        tool_use_correctness = "partial"
    else:
        tool_use_correctness = "pass"

    min_tool_calls_satisfied = len(tool_trace) >= scenario.expected_min_tool_calls
    if (
        not min_tool_calls_satisfied or not expected_chain_satisfied
    ) and tool_use_correctness == "pass":
        tool_use_correctness = "partial"

    answer = output.answer or ""
    if scenario.score_answer_last_turn_only and "\n---\n" in answer:
        answer = answer.rsplit("\n---\n", 1)[-1].strip()
    trace_summary = summarize_run_trace(
        run_id=output.run_id,
        events=_runtime_events_for_trace_summary(events),
        user_prompt=scenario.prompt,
        assistant_text=answer,
        task_contract=_scenario_task_contract(scenario),
    )
    research_efficiency = trace_summary.get("research_efficiency")
    if not isinstance(research_efficiency, dict):
        research_efficiency = {}
    llm_block = trace_summary.get("llm")
    llm_usage = (
        llm_block.get("usage")
        if isinstance(llm_block, dict) and isinstance(llm_block.get("usage"), dict)
        else {}
    )
    has_assertions = bool(
        scenario.expected_answer_contains or scenario.expected_answer_any_of
    )
    answer_relevance = "pass" if answer.strip() and not has_assertions else "fail"
    if scenario.relax_answer_when_tools_pass and tool_use_correctness == "pass":
        answer_relevance = "pass" if answer.strip() else "fail"
    elif has_assertions:
        if _answer_matches_expectations(answer=answer, scenario=scenario):
            answer_relevance = "pass" if answer.strip() else "fail"
        else:
            answer_relevance = "partial" if answer.strip() else "fail"

    efficiency = "pass" if len(events) <= max(1, scenario.max_steps * 4) else "partial"
    tool_results_list = tool_results if isinstance(tool_results, list) else []
    forbidden_imports_used: list[str] = []
    if "python_import_policy" in scenario.tags:
        forbidden_imports_used = _forbidden_python_imports_after_first_python(
            tool_results_list
        )
    bug_tags = classify_bug_tags(
        status=output.status.value,
        terminal_reason=(
            output.terminal_reason.value if output.terminal_reason else None
        ),
        expected_tools_missing=expected_missing,
        forbidden_tools_used=forbidden_used,
        empty_tool_results=empty_tool_results,
        repeated_tools=repeated_tools,
        forbidden_python_imports=forbidden_imports_used,
    )
    trace_failures = trace_summary.get("failures")
    if isinstance(trace_failures, dict):
        for key in (
            "deep_research_no_report_artifact",
            "deep_research_missing_initial_todo",
            "deep_research_long_final_after_report",
        ):
            if trace_failures.get(key) is True:
                bug_tags.append(key)
        if any(tag.startswith("deep_research_") for tag in bug_tags):
            efficiency = "fail"
    bug_tags = _dedupe_strings(bug_tags)

    runtime_step_count_raw = (
        metadata.get("step_count") if isinstance(metadata, dict) else None
    )
    runtime_step_count = (
        int(runtime_step_count_raw) if isinstance(runtime_step_count_raw, int) else None
    )

    return EvalSummary(
        scenario_id=scenario.scenario_id,
        run_id=output.run_id,
        status=output.status.value,
        terminal_reason=(
            output.terminal_reason.value if output.terminal_reason else None
        ),
        steps_total=len(events),
        llm_calls=llm_calls,
        tool_calls=len(tool_trace),
        tools_by_status=tools_by_status,
        tools_by_name_status=tools_by_name_status,
        repeated_tools=repeated_tools,
        repeated_tool_arguments=repeated_tool_arguments,
        actual_tool_chain=actual_tool_chain,
        expected_chain_satisfied=expected_chain_satisfied,
        min_tool_calls_satisfied=min_tool_calls_satisfied,
        required_tools_missing=required_missing,
        runtime_step_count=runtime_step_count,
        llm_usage=llm_usage,
        research_efficiency=research_efficiency,
        empty_tool_results=empty_tool_results,
        interrupts_or_denials=interrupts_or_denials,
        answer_length=len(answer),
        answer_language=_detect_answer_language(answer),
        elapsed_ms=elapsed_ms,
        expected_tools_missing=expected_missing,
        forbidden_tools_used=forbidden_used,
        answer_relevance=answer_relevance,
        tool_use_correctness=tool_use_correctness,
        efficiency=efficiency,
        notes="manual review pending",
        bug_tags=bug_tags,
    )


def _runtime_events_for_trace_summary(
    events: list[RuntimeEvent],
) -> list[dict[str, object]]:
    return [
        {
            "event": event.type.value,
            "data": event.payload,
        }
        for event in events
    ]


def _scenario_task_contract(scenario: EvalScenario) -> dict[str, Any] | None:
    if "deep_research" not in scenario.tags:
        return None
    return {
        "deep_research": True,
        "artifact_required": True,
        "requires_research": True,
    }


def _dedupe_strings(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


__all__ = [
    "EvalScenario",
    "EvalSummary",
    "LiveEvalSkipped",
    "can_run_provider",
    "classify_bug_tags",
    "default_deep_scenarios",
    "default_live_scenarios",
    "assert_eval_scenario_tool_packs_are_tuples",
    "default_regression_scenarios",
    "default_smoke_scenarios",
    "is_live_eval_enabled",
    "live_scenarios_for_suite",
    "render_eval_inspect",
    "render_eval_timeline",
    "run_live_evaluation",
    "summarize_run",
]
