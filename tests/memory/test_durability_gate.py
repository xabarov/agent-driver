"""Stage 4 memory durability and evidence-gate invariants."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.decision_contracts import DecisionAnswer, DecisionResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.llm.rollout import JevRolloutController, JevRolloutSettings
from agent_driver.memory import (
    FactExtractingMemoryProvider,
    InMemoryMemoryStore,
    MemoryDurabilityGate,
    MemoryRecord,
    MemoryTurn,
)


def _choice(key: str, selected: str, *, confidence: float = 0.9) -> DecisionAnswer:
    labels = ("durable", "session_only", "obsolete", "sensitive", "uncertain")
    remainder = (1.0 - confidence) / (len(labels) - 1)
    probabilities = {
        label: confidence if label == selected else remainder for label in labels
    }
    return DecisionAnswer(
        question_id=key,
        type="choice",
        choice=selected,
        probabilities=probabilities,
        confidence=confidence,
    )


class _DecisionStub:
    def __init__(self, categories: list[str], contradictions: list[float]) -> None:
        self.categories = categories
        self.contradictions = contradictions
        self.states: list[dict] = []

    async def decide(self, *, state, questions, model=None):
        self.states.append(state)
        answers = {}
        for key, question in questions.items():
            index = int(key.split("_")[1])
            if question.type == "choice":
                answers[key] = _choice(key, self.categories[index])
            else:
                answers[key] = DecisionAnswer(
                    question_id=key,
                    type="noul",
                    noul=self.contradictions[index],
                )
        return DecisionResponse(
            model="typesafe/jev-1.13",
            model_version="typesafe/jev-1.13-test",
            request_id="memory-gate-test",
            provider="stub",
            answers=answers,
            input_tokens=20,
            output_tokens=10,
            cost_usd=0.0002,
        )


@pytest.mark.asyncio
async def test_gate_accepts_only_durable_and_redacts_sensitive_candidates() -> None:
    provider = _DecisionStub(
        ["durable", "session_only", "sensitive", "sensitive"],
        [0.1, 0.1, 0.1, 0.1],
    )
    gate = MemoryDurabilityGate(decision_provider=provider)
    result = await gate.classify(
        turn=MemoryTurn(session_id="s", run_id="r"),
        candidates=[
            {"text": "User prefers CSV output.", "slot": "format"},
            {"text": "The current task is running.", "slot": "task"},
            {"text": "API key: sk-test-secret-value-12345", "slot": "secret"},
            {"text": "Contact: alice@example.com", "slot": "contact"},
        ],
        existing_records=[],
    )

    assert [item["text"] for item in result.accepted] == ["User prefers CSV output."]
    assert [item["text"] for item in result.session_only] == [
        "The current task is running."
    ]
    assert not result.held
    assert result.receipt["rejected_count"] == 2
    assert "sk-test-secret-value-12345" not in json.dumps(provider.states)
    assert "alice@example.com" not in json.dumps(provider.states)
    assert "User prefers CSV output." not in json.dumps(result.receipt)


@pytest.mark.asyncio
async def test_gate_holds_contradictory_durable_fact() -> None:
    provider = _DecisionStub(["durable"], [0.9])
    gate = MemoryDurabilityGate(decision_provider=provider)
    result = await gate.classify(
        turn=MemoryTurn(session_id="s", run_id="r"),
        candidates=[{"text": "User prefers JSON output.", "slot": "format"}],
        existing_records=[
            MemoryRecord(
                session_id="s",
                text="User prefers CSV output.",
                metadata={"slot": "format", "fact_id": "fact_old"},
            )
        ],
    )

    assert not result.accepted
    assert len(result.held) == 1
    assert result.receipt["decisions"][0]["action"] == "hold_contradiction"


@pytest.mark.asyncio
async def test_gate_failure_holds_candidates_without_writing() -> None:
    class _Broken:
        async def decide(self, **kwargs):
            raise RuntimeError("provider unavailable")

    result = await MemoryDurabilityGate(decision_provider=_Broken()).classify(
        turn=MemoryTurn(session_id="s", run_id="r"),
        candidates=[{"text": "User prefers CSV output."}],
        existing_records=[],
    )

    assert not result.accepted
    assert len(result.held) == 1
    assert result.receipt["source"] == "fallback"
    assert result.receipt["fallback_reason"] == "RuntimeError"


class _ExtractionStub(FakeProvider):
    async def complete(self, request):
        response = await super().complete(request)
        metadata = dict(response.message.metadata or {})
        metadata["planned_tool_calls"] = [
            {
                "name": "emit_result",
                "args": {
                    "facts": [
                        {"text": "User prefers CSV output.", "slot": "format"},
                        {"text": "The current task is running.", "slot": "task"},
                    ]
                },
            }
        ]
        return response.model_copy(
            update={"message": response.message.model_copy(update={"metadata": metadata})}
        )


@pytest.mark.asyncio
async def test_fact_provider_writes_provenance_only_for_durable_candidates() -> None:
    store = InMemoryMemoryStore()
    gate = MemoryDurabilityGate(
        decision_provider=_DecisionStub(["durable", "session_only"], [0.1, 0.1])
    )
    provider = FactExtractingMemoryProvider(
        store,
        _ExtractionStub(response_text="ignored"),
        durability_gate=gate,
    )
    await provider.sync_turn(
        MemoryTurn(
            session_id="s",
            run_id="r",
            user_text="Remember my output preference.",
            assistant_text="Done.",
        )
    )

    records = store.list_for_session("s")
    assert len(records) == 1
    assert records[0].text == "User prefers CSV output."
    assert records[0].metadata["memory_scope"] == "durable"
    assert records[0].metadata["source_ref"] == "run:r"
    assert records[0].metadata["fact_id"].startswith("fact_")
    receipt = provider.consume_durability_result("r", "s")
    assert receipt is not None
    assert receipt["receipt"]["accepted_count"] == 1
    assert isinstance(receipt["usage"], UsageSummary)


@pytest.mark.asyncio
async def test_lifecycle_projects_gate_receipt_provenance_and_usage() -> None:
    from agent_driver.runtime.single_agent.lifecycle.memory_hook import (
        MemoryLifecycleHook,
    )

    store = InMemoryMemoryStore()
    provider = FactExtractingMemoryProvider(
        store,
        _ExtractionStub(response_text="ignored"),
        durability_gate=MemoryDurabilityGate(
            decision_provider=_DecisionStub(["durable", "session_only"], [0.1, 0.1])
        ),
    )
    hook = MemoryLifecycleHook(provider)
    context = SimpleNamespace(
        run_id="r1",
        metadata={},
        run_input=SimpleNamespace(
            thread_id="s1", input="remember this", app_metadata={}
        ),
    )

    await hook.on_run_completed(context, answer="done")
    await hook.shutdown()

    assert context.metadata["memory_durability"]["accepted_count"] == 1
    assert context.metadata["memory_fact_provenance"][0]["source_ref"] == "run:r1"
    assert (
        context.metadata["cost_ledger"]["per_model"]["typesafe/jev-1.13"][
            "cost_usd"
        ]
        == 0.0002
    )


@pytest.mark.asyncio
async def test_memory_shadow_does_not_persist_and_observes_controller() -> None:
    store = InMemoryMemoryStore()
    controller = JevRolloutController(
        JevRolloutSettings(mode="shadow", min_observations_for_rollback=1)
    )
    provider = FactExtractingMemoryProvider(
        store,
        _ExtractionStub(response_text="ignored"),
        durability_gate=MemoryDurabilityGate(
            decision_provider=_DecisionStub(["durable", "session_only"], [0.1, 0.1]),
            rollout_policy=controller,
        ),
    )

    await provider.sync_turn(
        MemoryTurn(
            session_id="s",
            run_id="r",
            user_text="Remember my output preference.",
            assistant_text="Done.",
        )
    )

    assert store.list_for_session("s") == []
    result = provider.consume_durability_result("r", "s")
    assert result is not None
    assert result["receipt"]["applied"] is False
    assert result["receipt"]["proposed_accepted_count"] == 1
    assert controller.status()["observations"] == 1
