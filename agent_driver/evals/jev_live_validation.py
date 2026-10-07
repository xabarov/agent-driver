"""Bounded JEV decisions through production rollout hooks.

Each case makes one live decision in shadow, then replays the exact typed
response in active. Off makes no request. Runtime mutations and storage writes
are measured, not simulated. This isolates rollout from provider variance;
generation/answer quality and independent shadow/active latency are NOT measured.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any

from agent_driver.contracts import AgentRunInput, ToolCall, ToolManifest, ToolPolicyInput
from agent_driver.contracts.messages import ChatMessage
from agent_driver.evals.jev_compaction_benchmark import _load_local_env
from agent_driver.llm import (
    DecisionClientSettings, DecisionTimeoutError, FakeProvider, JevQualityGate,
    JevTierRouter, LlmRequest, OpenRouterDecisionClient,
)
from agent_driver.llm.rollout import JevRolloutController, JevRolloutSettings
from agent_driver.memory import (
    FactExtractingMemoryProvider, InMemoryMemoryStore, MemoryDurabilityGate,
    MemoryRecord, MemoryTurn,
)
from agent_driver.runtime.single_agent.llm_step import _maybe_llm_route
from agent_driver.runtime.single_agent.tool_stage import _maybe_quality_gate, _maybe_recovery_gate
from agent_driver.runtime.single_agent.types import RunContext, RunnerConfig
from agent_driver.runtime.tools import ToolExecutionResult
from agent_driver.tools import GovernedToolExecutor, ToolRegistry

SCHEMA = "jev-live-validation.v1"


@dataclass(frozen=True)
class ValidationCase:
    case_id: str
    surface: str
    categories: tuple[str, ...]
    request: str
    candidate: str = ""
    expected: tuple[str, ...] = ()


CASES = (
    ValidationCase("routing.simple", "routing", ("routing",),
                   "Return the uppercase form of the word cat.", expected=("fast",)),
    ValidationCase("routing.reasoning_ru", "routing", ("routing", "multilingual"),
                   "Докажи сходимость распределённого протокола при сетевых разделениях, "
                   "объясни невозможные гарантии и обоснуй компромиссы.", expected=("strong",)),
    ValidationCase("quality.grounded", "quality", ("escalation",),
                   "What is 2 + 2?", "4", ("accept",)),
    ValidationCase("quality.wrong_answer", "quality", ("escalation",),
                   "Check whether the proof 'all primes are odd because 3 is prime' is valid. "
                   "Explain the logical errors without external tools.",
                   "The proof is valid: one example proves the statement for every prime.",
                   ("escalate_strong",)),
    ValidationCase("quality.ambiguity_ru", "quality", ("escalation", "multilingual"),
                   "Сравни эти два документа. Документы ещё не приложены, их названия неизвестны.",
                   "Первый документ лучше второго.", ("ask_user",)),
    ValidationCase("recovery.denied", "recovery", ("tool_safety",),
                   "Write the report using write_file; the host policy denies that tool.",
                   "The write was denied; report the limitation.", ("accept", "ask_user")),
    ValidationCase("memory.mixed", "memory", ("memory", "corrections", "multilingual"),
                   "Remember stable preferences; hold unresolved conflicting facts."),
)

_CANDIDATES = (
    {"text": "Пользователь всегда предпочитает ответы на русском языке.", "slot": "language"},
    {"text": "The temporary report job is running this minute.", "slot": "task"},
    {"text": "API key: synthetic-validation-secret", "slot": "secret"},
    {"text": "User prefers JSON output.", "slot": "format"},
)
def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class _PairedDecision:
    """Capture once; reject an active replay against different state/questions."""

    def __init__(self, provider: Any):
        self.provider = provider
        self.response = None
        self.error: Exception | None = None
        self.fingerprint: str | None = None
        self.calls = 0
        self.replays = 0
        self.elapsed_ms = 0.0
        self.sensitive_redacted = True
        self.replay_mismatch = False

    async def decide(self, *, state, questions, model=None):
        fingerprint = _hash({"state": state, "questions": {
            key: question.model_dump() for key, question in questions.items()
        }, "model": model})
        self.sensitive_redacted &= "synthetic-validation-secret" not in json.dumps(state)
        if self.fingerprint is not None:
            self.replays += 1
            if fingerprint != self.fingerprint:
                self.replay_mismatch = True
                raise ValueError("paired_decision_state_changed")
        else:
            self.fingerprint = fingerprint
            self.calls += 1
            start = perf_counter()
            try:
                self.response = await self.provider.decide(state=state, questions=questions, model=model)
            except Exception as exc:
                self.error = exc
            self.elapsed_ms = (perf_counter() - start) * 1000
        if self.error is not None:
            raise self.error
        return self.response.model_copy(deep=True)

    def receipt(self) -> dict[str, Any]:
        response = self.response
        return {
            "calls": self.calls, "replays": self.replays,
            "input_sha256": self.fingerprint, "latency_ms": round(self.elapsed_ms, 2),
            "error": type(self.error).__name__ if self.error else None,
            "cost_usd": response.cost_usd if response else None,
            "model": response.model if response else None,
            "model_version": response.model_version if response else None,
            "request_id": response.request_id if response else None,
            "input_tokens": response.input_tokens if response else None,
            "output_tokens": response.output_tokens if response else None,
        }


class _Host:
    def __init__(self, config):
        self._config = config
        self.events = []

    def _emit_runtime_decision(self, context, **event):
        self.events.append(event)


class _FixtureExtractor(FactExtractingMemoryProvider):
    """Fixed extraction isolates live durability from generative extraction."""
    async def _extract_facts(self, turn):
        return [dict(item) for item in _CANDIDATES]


async def _exercise(case: ValidationCase, mode: str, probe: _PairedDecision, model: str):
    policy = JevRolloutController(JevRolloutSettings(mode=mode))
    config = RunnerConfig(
        jev_rollout=policy,
        model_router=JevTierRouter(decision_provider=probe, model=model),
        quality_gate=JevQualityGate(decision_provider=probe, model=model),
    )
    host = _Host(config)
    context = RunContext(
        run_input=AgentRunInput(
            input=case.request, agent_id="jev-validation", graph_preset="single_react",
            model_role="balanced", app_metadata={"jev_task": "validation"},
            tool_policy=ToolPolicyInput(denied_tools=["write_file"]),
        ),
        identifiers={"run_id": case.case_id, "attempt_id": "1"},
        metadata={},
    )
    context.llm_response = await FakeProvider(response_text=case.candidate).complete(
        LlmRequest(messages=[ChatMessage(role="user", content=case.request)])
    )
    invariants: dict[str, bool] = {}
    if case.surface == "routing":
        await _maybe_llm_route(host, context)
        receipt = context.metadata.get("llm_route_decision", {})
        outcome = context.metadata.get("llm_routed_role", "balanced")
        correct = receipt.get("role") in case.expected
        effect = {"role": outcome}
    elif case.surface == "memory":
        store = InMemoryMemoryStore()
        store.append(MemoryRecord(session_id="s", text="User prefers CSV output.",
                                  metadata={"slot": "format", "fact_id": "fact_existing"}))
        memory = _FixtureExtractor(
            store, FakeProvider(),
            durability_gate=MemoryDurabilityGate(
                decision_provider=probe, model=model, rollout_policy=policy,
            ),
        )
        await memory.sync_turn(MemoryTurn(session_id="s", run_id=case.case_id))
        record = memory.consume_durability_result(case.case_id, "s")
        receipt = record["receipt"] if record else {}
        written = [item for item in store.list_for_session("s") if item.seq > 1]
        actions = [item["action"] for item in receipt.get("decisions", [])]
        # The live model may choose ``hold_uncertain`` instead of the more
        # specific contradiction label. Both are safe outcomes here. The oracle
        # is therefore an invariant, not an exact wording match: the sensitive
        # candidate must never be accepted, transient state must not be durable,
        # and a conflicting preference must not overwrite the existing fact.
        correct = (
            len(actions) == len(_CANDIDATES)
            and actions[0] in {"accept_durable", "hold_uncertain"}
            and actions[1] in {"session_only", "hold_uncertain"}
            and actions[2].startswith("reject_")
            and actions[3] in {"hold_contradiction", "hold_uncertain"}
        )
        effect = {"writes": len(written), "record_hashes": sorted(_hash(item.text) for item in written)}
        outcome = actions
        invariants = {
            "memory_write_invariant": all(item.text == _CANDIDATES[0]["text"] for item in written),
            "memory_provenance": all(item.metadata.get("source_ref") == f"run:{case.case_id}" for item in written),
            "sensitive_redacted": probe.sensitive_redacted,
        }
    else:
        result = ToolExecutionResult()
        if case.surface == "recovery":
            registry = ToolRegistry()
            executions = []
            def handler(args):
                executions.append(args)
                return {"ok": True}
            registry.register(ToolManifest(name="write_file", description="Synthetic write counter"), handler)
            executor = GovernedToolExecutor(registry=registry)
            call_response = await FakeProvider().complete(LlmRequest(
                messages=[ChatMessage(role="user", content=case.request)],
                metadata={"planned_tool_calls": [ToolCall(tool_name="write_file", args={}).model_dump(mode="json")]},
            ))
            before_policy = context.run_input.tool_policy.model_dump()
            governed = await executor.execute(context.run_input, call_response)
            result = ToolExecutionResult(envelopes=governed.envelopes)
            transitioned = await _maybe_recovery_gate(host, context, result)
            # Attempt the same call again after the JEV control step, proving the
            # real executor still denies it and the handler never runs.
            after = await executor.execute(context.run_input, call_response)
            invariants["policy_denial_invariant"] = bool(
                after.envelopes and all(item.decision.value == "deny" for item in after.envelopes)
                and not executions and before_policy == context.run_input.tool_policy.model_dump()
            )
            receipt = context.metadata.get("quality_gate_recovery_decision", {})
        else:
            transitioned = await _maybe_quality_gate(host, context, result)
            receipt = context.metadata.get("quality_gate_decision", {})
        outcome = receipt.get("action", "accept") if mode == "active" else "accept"
        correct = receipt.get("action") in case.expected
        effect = {
            "transitioned": transitioned,
            "role": context.metadata.get("llm_routed_role", "balanced"),
            "protocol_sha256": _hash(context.metadata.get("protocol_messages", [])),
        }
    return {
        "mode": mode, "effect": effect, "outcome": outcome,
        "decision_correct": correct if mode != "off" else None,
        "fallback": receipt.get("source") == "fallback",
        "confidence": receipt.get("confidence"),
        "source": receipt.get("source", "disabled"),
        "controller_observations": policy.status()["observations"],
        "invariants": invariants,
    }


class _Timeout:
    async def decide(self, **kwargs):
        raise DecisionTimeoutError()


async def _rollback_check(model: str) -> bool:
    controller = JevRolloutController(JevRolloutSettings(
        mode="active", max_fallback_rate=0.0, min_observations_for_rollback=1,
    ))
    probe = _PairedDecision(_Timeout())
    host = _Host(RunnerConfig(jev_rollout=controller,
                             model_router=JevTierRouter(decision_provider=probe, model=model)))
    for run_id in ("first", "after-rollback"):
        ctx = RunContext(run_input=AgentRunInput(input="hello", agent_id="probe", graph_preset="single_react"),
                         identifiers={"run_id": run_id, "attempt_id": "1"}, metadata={})
        await _maybe_llm_route(host, ctx)
    return controller.status()["rolled_back"] and probe.calls == 1 and probe.replays == 0 and "llm_route_decision" not in ctx.metadata


async def run_live_validation(*, decision_provider: Any, decision_model: str = "typesafe/jev-1.13",
                              live: bool = False, repeats: int = 1) -> dict[str, Any]:
    if repeats < 1:
        raise ValueError("repeats must be positive")
    rows = []
    for repeat in range(repeats):
        for case in CASES:
            probe = _PairedDecision(decision_provider)
            treatments = [await _exercise(case, mode, probe, decision_model) for mode in ("off", "shadow", "active")]
            off, shadow, active = treatments
            rows.append({
                "case_id": case.case_id, "repeat": repeat, "surface": case.surface,
                "categories": list(case.categories), "live": live,
                "treatments": treatments, "call": probe.receipt(),
                "shadow_behavior_unchanged": off["effect"] == shadow["effect"],
                "active_effect_observed": off["effect"] != active["effect"],
                "paired_replay_valid": not probe.replay_mismatch and probe.calls == 1 and probe.replays == 1,
            })
    calls = [row["call"] for row in rows]
    active = [row["treatments"][2] for row in rows]
    cost_complete = all(call["cost_usd"] is not None for call in calls)
    quality = mean(row["decision_correct"] for row in active)
    fallback = mean(row["fallback"] for row in active)
    invariants = all(all(item["invariants"].values()) for row in rows for item in row["treatments"])
    shadow_ok = all(row["shadow_behavior_unchanged"] for row in rows)
    paired_ok = all(row["paired_replay_valid"] for row in rows)
    rollback_ok = await _rollback_check(decision_model)
    metrics = {
        "cases": len(CASES), "repeats": repeats, "decision_calls": len(calls),
        "treatments": len(rows) * 3, "decision_accuracy": quality,
        "fallback_rate": fallback, "cost_complete": cost_complete,
        "known_cost_usd": sum(call["cost_usd"] or 0 for call in calls),
        "cost_usd": sum(call["cost_usd"] for call in calls) if cost_complete else None,
        "latency_ms": mean(call["latency_ms"] for call in calls),
        "shadow_behavior_unchanged": shadow_ok, "invariants_passed": invariants,
        "paired_replay_valid": paired_ok, "rollback_check": rollback_ok,
        "active_effects_observed": sum(row["active_effect_observed"] for row in rows),
    }
    telemetry = getattr(decision_provider, "telemetry", None)
    telemetry_snapshot = telemetry.snapshot() if callable(getattr(telemetry, "snapshot", None)) else None
    return {
        "schema": SCHEMA, "created_at": datetime.now(UTC).isoformat(), "live": live,
        "decision_model": decision_model, "pairing": "one_call_shadow_active_replay",
        "generation_quality_measured": False,
        "memory_baseline": "gate_configured_off_holds_all_new_writes",
        "categories": sorted({category for row in rows for category in row["categories"]}),
        "metrics": metrics,
        "transport_telemetry": telemetry_snapshot,
        "passed": quality == 1.0 and fallback == 0.0 and invariants and shadow_ok and paired_ok and rollback_ok,
        "rows": rows,
    }


def render_live_validation(report: dict[str, Any]) -> str:
    m = report["metrics"]
    lines = ["# JEV runtime live validation", "", f"Run: {report['created_at']}; live: {report['live']}.", "",
             "One live decision per case/repeat in shadow; its typed response is replayed through active runtime hooks. "
             "Off calls no provider. Generation is fixed; answer quality is not measured. "
             "Costs count each network call once. Memory baseline is an installed gate in off mode (new writes held).",
             "", f"Gate: {'PASS' if report['passed'] else 'HOLD'}.", "", "| Metric | Value |", "|---|---:|"]
    lines += [f"| {key} | {value} |" for key, value in m.items()]
    lines += ["", "| Case | Repeat | Proposed active outcome | Correct | Shadow unchanged | Active changed |", "|---|---:|---|---|---|---|"]
    for row in report["rows"]:
        active = row["treatments"][2]
        lines.append(f"| {row['case_id']} | {row['repeat']} | {active['outcome']} | {active['decision_correct']} | {row['shadow_behavior_unchanged']} | {row['active_effect_observed']} |")
    return "\n".join(lines) + "\n"


def write_live_validation_artifacts(report: dict[str, Any], output: Path) -> tuple[Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    json_path, markdown_path = output / "report.json", output / "report.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown_path.write_text(render_live_validation(report), encoding="utf-8")
    return json_path, markdown_path


async def _main(args) -> None:
    _load_local_env()
    key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("AGENT_DRIVER_API_KEY")
    if not key:
        raise ValueError("OpenRouter key is required")
    model = os.environ.get("AGENT_DRIVER_JEV_MODEL", "typesafe/jev-1.13")
    provider = OpenRouterDecisionClient(api_key=key, model=model,
        base_url=os.environ.get("AGENT_DRIVER_BASE_URL", "https://openrouter.ai/api/v1"),
        settings=DecisionClientSettings(timeout_s=args.timeout, max_attempts=1),
        reuse_connections=args.reuse_connections)
    report = await run_live_validation(decision_provider=provider, decision_model=model, live=True, repeats=args.repeats)
    paths = write_live_validation_artifacts(report, args.output)
    print(f"Report: {paths[1]}; passed={report['passed']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True, help="Use OpenRouter; incurs API charges")
    parser.add_argument("--output", type=Path, default=Path("artifacts/jev-stage5/live-validation"))
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--reuse-connections", action="store_true",
                        help="Keep one HTTP connection pool for the run")
    args = parser.parse_args()
    if args.timeout <= 0 or args.repeats < 1:
        parser.error("timeout and repeats must be positive")
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
