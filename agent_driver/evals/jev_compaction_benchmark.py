"""Measured JEV compaction benchmark using the production compaction stage.

Offline mode exercises plumbing with an explicitly labelled oracle stub and an
identity summarizer. Only live mode measures semantic decisions and answer quality.
Run with ``python -m agent_driver.evals.jev_compaction_benchmark --help``.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from typing import Any

from agent_driver.context import CompactionOrchestrator
from agent_driver.context.artifacts import InMemoryArtifactStore
from agent_driver.context.compaction.llm_full import REQUIRED_SUMMARY_KEYS
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.evals.jev_compaction_scenarios import (
    CompactionScenario,
    synthetic_compaction_scenarios,
)
from agent_driver.llm import (
    CompactionUnit,
    DecisionAnswer,
    DecisionClientSettings,
    DecisionResponse,
    DecisionTimeoutError,
    FakeProvider,
    JevCompactionPrepass,
    LlmRequest,
    OpenAICompatibleProvider,
    OpenRouterDecisionClient,
)
from agent_driver.runtime.single_agent.context_management.compaction_stage import (
    _compaction_prepass_units,
    apply_compaction_if_eligible,
)
from agent_driver.runtime.single_agent.types import RunContext, RunnerConfig


def _load_local_env(path: Path = Path(".env")) -> None:
    """Load simple KEY=VALUE entries without printing or requiring dotenv."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class _DecisionProbe:
    """Keep raw-free answers and usage even when the pre-pass falls back."""

    def __init__(self, provider: Any):
        self.provider = provider
        self.calls = 0
        self.responses: list[dict[str, Any]] = []

    async def decide(self, **kwargs):
        self.calls += 1
        response = await self.provider.decide(**kwargs)
        self.responses.append(response.model_dump(mode="json"))
        return response


class _TimeoutProvider:
    async def decide(self, **kwargs):
        raise DecisionTimeoutError()


class _OracleStub:
    """Plumbing-only simulation. Oracle labels never reach live providers."""

    def __init__(self, units: list[CompactionUnit], scenario: CompactionScenario):
        self.archive = {
            u.unit_id for u in units
            if set(u.message_indexes) <= scenario.archive_message_indexes
        }

    async def decide(self, *, state, questions, model=None):
        answers = {}
        for key, question in questions.items():
            index = int(key.split("_")[1])
            unit = state["units"][index]
            archive = unit["id"] in self.archive
            if question.type == "score":
                level = 0 if archive else 3
                answers[key] = DecisionAnswer(
                    question_id=key, type="score", score=float(level), confidence=1.0,
                    probabilities={str(i): float(i == level) for i in range(4)},
                )
            else:
                answers[key] = DecisionAnswer(
                    question_id=key, type="noul",
                    noul=0.99 if key.endswith("safe_to_remove") and archive else 0.01,
                )
        return DecisionResponse(model="offline/oracle-stub", answers=answers, cost_usd=0.0)


class _GenerationProbe:
    """Record actual billed usage, input sizes and latency for each completion."""

    name = "benchmark-generation"

    def __init__(self, provider: Any | None):
        self.provider = provider
        self.calls: list[dict[str, Any]] = []

    async def complete(self, request):
        request = request.model_copy(update={
            "temperature": 0.0, "max_tokens": 2400,
            "reasoning": {"enabled": False},
        })
        started = perf_counter()
        prompt = "\n".join(m.content for m in request.messages)
        if self.provider is None:
            # Copy context; do not simulate semantic quality or real token billing.
            summary = {key: [] for key in REQUIRED_SUMMARY_KEYS}
            summary["key_concepts"] = ["offline_identity_compaction"]
            response = await FakeProvider(
                response_text="<persisted_summary>" + json.dumps(summary) + "</persisted_summary>"
            ).complete(request)
        else:
            response = await self.provider.complete(request)
        self.calls.append({
            "phase": "compaction" if request.metadata.get("compaction_mode") else "answer",
            "prompt_chars": len(prompt), "prompt_sha256": _sha(prompt),
            "latency_ms": round((perf_counter() - started) * 1000, 2),
            "model": response.model,
            "usage": response.usage.model_dump(mode="json"),
            "tokens_are_estimates": self.provider is None,
        })
        return response


def _parse_answer(text: str) -> dict[str, Any] | None:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        answer = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return answer if isinstance(answer, dict) else None


def _score_answer(actual: dict[str, Any] | None, expected: dict[str, str]) -> float:
    if actual is None:
        return 0.0
    return sum(str(actual.get(key)) == value for key, value in expected.items()) / len(expected)


async def run_case(
    scenario: CompactionScenario, *, mode: str, repeat: int = 0,
    decision_provider: Any = None, generation_provider: Any = None,
    live: bool = False, decision_model: str = "typesafe/jev-1.13", generation_model: str = "offline/identity",
    min_confidence: float = 0.55, archive_probability: float = 0.70,
    archive_relevance_max: float = 1.5, retain_relevance_min: float = 2.0,
    retain_probability: float = 0.55,
) -> dict[str, Any]:
    messages = [m.model_copy(deep=True) for m in scenario.messages]
    original = json.dumps([m.model_dump(mode="json") for m in messages], sort_keys=True)
    units = _compaction_prepass_units(messages)
    probe = _DecisionProbe(
        _TimeoutProvider() if mode == "forced_fallback"
        else decision_provider if live else _OracleStub(units, scenario)
    )
    generator = _GenerationProbe(generation_provider if live else None)
    config = RunnerConfig(
        enable_compaction=True, enable_llm_compaction=True,
        enable_jev_compaction_prepass=mode != "baseline",
        compaction_prepass=JevCompactionPrepass(
            decision_provider=probe,
            model=decision_model,
            min_confidence=min_confidence,
            archive_probability=archive_probability,
            archive_relevance_max=archive_relevance_max,
            retain_relevance_min=retain_relevance_min,
            retain_probability=retain_probability,
        ),
        # Isolate the JEV effect: do not let unrelated old-result pruning or PTL
        # truncation remove the same input before it can be measured.
        live_tool_prune_enabled=False, enable_ptl_retry=False,
        enable_tool_arg_truncation=False, enable_tool_history_compression=False,
        compaction_model=generation_model, aux_idle_timeout_seconds=None,
    )
    orchestrator = CompactionOrchestrator()
    events = []
    host = SimpleNamespace(
        _config=config,
        _deps=SimpleNamespace(provider=generator, artifact_store=InMemoryArtifactStore()),
        _get_compaction_orchestrator=lambda: orchestrator, _emit=events.append,
    )
    context = RunContext(
        run_input=AgentRunInput(
            input=scenario.active_request or messages[-1].content,
            agent_id="benchmark",
            graph_preset="single_react",
        ),
        identifiers={"run_id": f"{scenario.name}-{mode}-{repeat}", "attempt_id": "1"},
        metadata={**scenario.metadata, "protocol_messages": json.loads(original)},
    )
    request = LlmRequest(model=generation_model, messages=messages)
    started = perf_counter()
    error = None
    try:
        await asyncio.wait_for(apply_compaction_if_eligible(
            host, context=context, request=request, token_pressure_state=scenario.pressure,
        ), timeout=120)
    except Exception as exc:  # benchmark records failures; it never calls them passes
        error = type(exc).__name__
    compaction_ms = (perf_counter() - started) * 1000
    compacted_text = "\n".join(m.content for m in request.messages)
    receipt = context.metadata.get("compaction_prepass", {})
    archived = {
        index for d in receipt.get("decisions", []) if d.get("retention") == "archive"
        for index in d.get("message_indexes", [])
    }
    expected_archive = set(scenario.archive_message_indexes)
    correct_archives = archived & expected_archive
    answer = None
    if live and error is None:
        try:
            response = await asyncio.wait_for(generator.complete(request), timeout=120)
            answer = _parse_answer(response.message.content)
        except Exception as exc:
            error = "answer_" + type(exc).__name__
    summary_calls = [call for call in generator.calls if call["phase"] == "compaction"]
    decision_usage = [response for response in probe.responses]
    costs = [call["usage"]["cost_usd_estimate"] for call in generator.calls]
    costs += [response["cost_usd"] for response in decision_usage]
    compaction_result = context.metadata.get("compaction_result") or {}
    protected_recall = (
        sum(fact in compacted_text for fact in scenario.protected_facts) / len(scenario.protected_facts)
        if scenario.protected_facts else None
    )
    return {
        "scenario": scenario.name, "mode": mode, "repeat": repeat, "live": live,
        "pressure": scenario.pressure, "error": error,
        "eligible": bool(context.metadata.get("compaction_decision", {}).get("eligible")),
        "compaction_success": compaction_result.get("success"),
        "decision_calls": probe.calls, "summary_calls": len(summary_calls),
        "compaction_latency_ms": round(compaction_ms, 2),
        "total_latency_ms": round((perf_counter() - started) * 1000, 2),
        "original_chars": sum(len(m.content) for m in messages),
        "compacted_chars": len(compacted_text),
        "compactor_prompt_chars": summary_calls[0]["prompt_chars"] if summary_calls else 0,
        "compactor_prompt_sha256": summary_calls[0]["prompt_sha256"] if summary_calls else None,
        "compactor_input_tokens": summary_calls[0]["usage"]["input_tokens"] if summary_calls else 0,
        "total_input_tokens": sum(c["usage"]["input_tokens"] for c in generator.calls) + sum(r["input_tokens"] or 0 for r in decision_usage),
        "total_cost_usd": sum(costs) if live and costs and all(c is not None for c in costs) and error is None else None,
        "cost_complete": bool(live and costs and all(c is not None for c in costs) and error is None),
        "archived_messages": len(archived), "expected_archive_messages": len(expected_archive),
        "archive_precision": len(correct_archives) / len(archived) if archived else None,
        "archive_recall": len(correct_archives) / len(expected_archive) if expected_archive else None,
        "protected_fact_recall": protected_recall,
        "context_fact_recall": sum(v in compacted_text for v in scenario.expected_answer.values()) / len(scenario.expected_answer),
        "answer_fact_accuracy": _score_answer(answer, scenario.expected_answer) if live else None,
        "answer_exact_match": answer is not None and _score_answer(answer, scenario.expected_answer) == 1.0 and set(answer) == set(scenario.expected_answer) if live else None,
        "answer": answer,
        "durable_transcript_unchanged": original == json.dumps(context.metadata["protocol_messages"], sort_keys=True),
        "prepass": receipt, "decision_responses": probe.responses, "generation_calls": generator.calls,
        "prepass_config": {
            "min_confidence": min_confidence,
            "archive_probability": archive_probability,
            "archive_relevance_max": archive_relevance_max,
            "retain_relevance_min": retain_relevance_min,
            "retain_probability": retain_probability,
        },
    }


def compare_rows(rows: list[dict[str, Any]]) -> None:
    """Pair repeats, including negative savings and strict fallback equivalence."""
    baselines = {(r["scenario"], r["repeat"]): r for r in rows if r["mode"] == "baseline"}
    for row in rows:
        base = baselines.get((row["scenario"], row["repeat"]))
        if not base:
            continue
        before = base["compactor_prompt_chars"]
        row["compactor_input_reduction_pct"] = 100 * (1 - row["compactor_prompt_chars"] / before) if before else 0.0
        row["same_compactor_input_as_baseline"] = row["compactor_prompt_sha256"] == base["compactor_prompt_sha256"]
        row["cost_delta_usd"] = (
            row["total_cost_usd"] - base["total_cost_usd"]
            if row["total_cost_usd"] is not None and base["total_cost_usd"] is not None else None
        )


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "# JEV synthetic compaction benchmark", "",
        f"Run: {report['created_at']}. Live: {report['live']}. Repeats: {report['repeats']}.", "",
        f"JEV thresholds: min confidence={report['prepass_config']['min_confidence']}, "
        f"archive probability={report['prepass_config']['archive_probability']}, "
        f"archive relevance max={report['prepass_config']['archive_relevance_max']}, "
        f"retain relevance min={report['prepass_config']['retain_relevance_min']}, "
        f"retain probability={report['prepass_config']['retain_probability']}.", "",
        "Production compaction stage and full structured summarizer; live rows additionally call a generation model and score its JSON answer against a hidden exact-value oracle. "
        "Other pruning tiers and PTL dropping are disabled to isolate JEV. Pressure is explicitly injected; the no-pressure control uses `ok`.", "",
        "Offline oracle stubs verify wiring only; their reductions are NOT evidence of JEV quality. "
        "Provider token usage is measured in live runs; offline tokens use the fake provider's char estimate. "
        "Costs include decision + summary + answer; unknown/failed-call billing stays unknown. "
        "Each row is one run. No statistical significance is claimed.", "",
        "| Scenario | Mode | Repeat | Archived | Compactor input reduction | Exact protected recall | Answer accuracy | Total USD | Compaction ms | Fallback/error |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in report["rows"]:
        def percent(x):
            return "—" if x is None else f"{100*x:.0f}%"
        cost = "—" if row["total_cost_usd"] is None else f"{row['total_cost_usd']:.6f}"
        reason = row["error"] or row["prepass"].get("fallback_reason") or "—"
        lines.append(
            f"| {row['scenario']} | {row['mode']} | {row['repeat']} | {row['archived_messages']} "
            f"| {row.get('compactor_input_reduction_pct', 0):.1f}% | {percent(row['protected_fact_recall'])} "
            f"| {percent(row['answer_fact_accuracy'])} | {cost} | {row['compaction_latency_ms']:.0f} | {reason} |"
        )
    lines.extend(["", "Machine-readable JSON includes per-call usage, raw-free JEV responses, input hashes, archive precision/recall and answer dictionaries.", ""])
    return "\n".join(lines)


async def _main(args):
    _load_local_env()
    scenarios = synthetic_compaction_scenarios()
    if args.scenario:
        selected = set(args.scenario)
        unknown = selected - {s.name for s in scenarios}
        if unknown:
            raise ValueError(f"Unknown scenarios: {sorted(unknown)}")
        scenarios = tuple(s for s in scenarios if s.name in selected)
    args.output.mkdir(parents=True, exist_ok=True)
    generation_provider = decision_provider = None
    decision_model = os.environ.get("AGENT_DRIVER_JEV_MODEL", "typesafe/jev-1.13")
    generation_model = os.environ.get("AGENT_DRIVER_FAST_MODEL") or os.environ.get("AGENT_DRIVER_MODEL", "offline/identity")
    if args.live:
        key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("AGENT_DRIVER_API_KEY")
        if not key or generation_model == "offline/identity":
            raise ValueError("Live mode requires configured OpenRouter key and generation model")
        base_url = os.environ.get("AGENT_DRIVER_BASE_URL", "https://openrouter.ai/api/v1")
        decision_provider = OpenRouterDecisionClient(
            api_key=key, model=decision_model, base_url=base_url,
            settings=DecisionClientSettings(timeout_s=30, max_attempts=1),
        )
        generation_provider = OpenAICompatibleProvider(config=OpenAICompatibleProvider.Config(
            name="openrouter-benchmark", api_key=key, base_url=base_url,
            model=generation_model, timeout_s=90, max_tokens_default=2400,
        ))
    modes = ("baseline", "jev" if args.live else "oracle_stub", "forced_fallback")
    rows = []
    # Rotate order each repeat to reduce systematic warm-cache ordering effects.
    with (args.output / "rows.jsonl").open("w") as journal:
        for repeat in range(args.repeats):
            for scenario in scenarios:
                ordered = modes[repeat % len(modes):] + modes[:repeat % len(modes)]
                for mode in ordered:
                    row = await run_case(
                        scenario, mode=mode, repeat=repeat, live=args.live,
                        decision_provider=decision_provider, generation_provider=generation_provider,
                        decision_model=decision_model, generation_model=generation_model,
                        min_confidence=args.min_confidence,
                        archive_probability=args.archive_probability,
                        archive_relevance_max=args.archive_relevance_max,
                        retain_relevance_min=args.retain_relevance_min,
                        retain_probability=args.retain_probability,
                    )
                    rows.append(row)
                    journal.write(json.dumps(row, ensure_ascii=False) + "\n")
                    journal.flush()
                    print(f"{scenario.name} {mode} #{repeat}: archived={row['archived_messages']} accuracy={row['answer_fact_accuracy']} fallback={row['prepass'].get('fallback_reason')} error={row['error']}", flush=True)
    compare_rows(rows)
    report = {
        "schema": "jev-compaction-benchmark.v1", "created_at": datetime.now(timezone.utc).isoformat(),
        "live": args.live, "repeats": args.repeats,
        "decision_model": decision_model, "generation_model": generation_model,
        "prepass_config": {
            "min_confidence": args.min_confidence,
            "archive_probability": args.archive_probability,
            "archive_relevance_max": args.archive_relevance_max,
            "retain_relevance_min": args.retain_relevance_min,
            "retain_probability": args.retain_probability,
        },
        "scenario_hashes": {s.name: _sha(json.dumps(asdict(s), default=str, sort_keys=True)) for s in scenarios},
        "rows": rows,
    }
    (args.output / "results.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    (args.output / "report.md").write_text(render_report(report))
    print(f"Report: {args.output / 'report.md'}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Use configured OpenRouter models; incurs API charges")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--scenario", action="append")
    parser.add_argument("--output", type=Path, default=Path("artifacts/jev-compaction/offline"))
    parser.add_argument("--min-confidence", type=float, default=0.55)
    parser.add_argument("--archive-probability", type=float, default=0.70)
    parser.add_argument("--archive-relevance-max", type=float, default=1.5)
    parser.add_argument("--retain-relevance-min", type=float, default=2.0)
    parser.add_argument("--retain-probability", type=float, default=0.55)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    if not 0 <= args.min_confidence <= 1:
        parser.error("--min-confidence must be between 0 and 1")
    if not 0.5 <= args.archive_probability <= 1:
        parser.error("--archive-probability must be between 0.5 and 1")
    if not 0 <= args.archive_relevance_max <= 3:
        parser.error("--archive-relevance-max must be between 0 and 3")
    if not 0 <= args.retain_relevance_min <= 3:
        parser.error("--retain-relevance-min must be between 0 and 3")
    if not 0.5 <= args.retain_probability <= 1:
        parser.error("--retain-probability must be between 0.5 and 1")
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
