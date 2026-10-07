"""Typed decision-client and JEV tier-router coverage."""

from __future__ import annotations

import json

import httpx
import pytest

from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.llm.decisions import (
    DecisionCircuitOpenError,
    DecisionClientSettings,
    DecisionProtocolError,
    DecisionQuestion,
    DecisionRequestError,
    OpenRouterDecisionClient,
)
from agent_driver.llm.decision_telemetry import DecisionTelemetry
from agent_driver.llm.model_router import JevTierRouter, RouteContext

_RUN = AgentRunInput(input="x", agent_id="a", graph_preset="single_react")


@pytest.mark.asyncio
async def test_pooled_client_records_bounded_transport_telemetry():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "typesafe/jev-1.13",
                "answers": {
                    "tier": {
                        "type": "choice",
                        "choice": "fast",
                        "probabilities": {"fast": 1.0, "balanced": 0.0},
                        "confidence": 1.0,
                    }
                },
                "usage": {"input_tokens": 1, "output_tokens": 1, "cost": 0.0},
            },
        )

    telemetry = DecisionTelemetry(window_size=2)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = OpenRouterDecisionClient(
            api_key="secret",
            client=http_client,
            settings=DecisionClientSettings(retry_backoff_s=0),
            reuse_connections=True,
            telemetry=telemetry,
        )
        questions = {
            "tier": DecisionQuestion(
                type="choice", instructions="Choose", criteria={"fast": "F", "balanced": "B"}
            )
        }
        await client.decide(state={"request": "x"}, questions=questions)
        await client.decide(state={"request": "y"}, questions=questions)
    snapshot = telemetry.snapshot()

    assert snapshot["calls"] == 2
    assert snapshot["outcomes"] == {"success": 2}
    assert snapshot["window_size"] == 2


@pytest.mark.asyncio
async def test_openrouter_decision_client_sends_typed_questions_and_parses_answers():
    seen: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "typesafe/jev-1.13-20260917",
                "id": "decision-1",
                "provider": "TypeSafe",
                "answers": {
                    "tier": {
                        "type": "choice",
                        "choice": "balanced",
                        "probabilities": {"fast": 0.1, "balanced": 0.8, "strong": 0.1},
                        "confidence": 0.8,
                    },
                    "requires_strong": {"type": "noul", "noul": 0.1},
                },
                "usage": {"input_tokens": 100, "output_tokens": 5, "cost": 0.01},
            },
            headers={"x-request-id": "request-1"},
        )

    client = OpenRouterDecisionClient(
        api_key="secret",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        settings=DecisionClientSettings(retry_backoff_s=0),
    )
    result = await client.decide(
        state={"request": "summarize this"},
        questions={
            "tier": DecisionQuestion(
                type="choice",
                instructions="Choose a tier",
                criteria={"fast": "simple", "balanced": "normal", "strong": "hard"},
            ),
            "requires_strong": DecisionQuestion(
                type="noul", instructions="Does this require strong reasoning?"
            ),
        },
    )

    assert seen["url"] == "https://openrouter.ai/api/alpha/decisions"
    assert seen["auth"] == "Bearer secret"
    payload = seen["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "typesafe/jev-1.13"
    assert payload["state"] == {"request": "summarize this"}
    assert payload["questions"]["tier"]["type"] == "choice"
    assert result.model_version == "typesafe/jev-1.13-20260917"
    assert result.request_id == "request-1"
    assert result.answers["tier"].choice == "balanced"
    assert result.answers["tier"].confidence == 0.8
    assert result.answers["requires_strong"].noul == 0.1
    assert result.cost_usd == 0.01


@pytest.mark.asyncio
async def test_openrouter_decision_client_rejects_malformed_answers():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"model": "typesafe/jev-1.13", "answers": {"tier": {"type": "bogus"}}},
        )

    client = OpenRouterDecisionClient(
        api_key="secret",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        settings=DecisionClientSettings(retry_backoff_s=0),
    )
    with pytest.raises(DecisionProtocolError):
        await client.decide(
            state={"request": "x"},
            questions={
                "tier": DecisionQuestion(
                    type="choice", instructions="Choose", criteria={"a": "A", "b": "B"}
                )
            },
        )


@pytest.mark.asyncio
async def test_openrouter_decision_client_retries_explicit_retryable_status():
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(
            200,
            json={
                "model": "typesafe/jev-1.13-20260917",
                "answers": {
                    "tier": {
                        "type": "choice",
                        "choice": "fast",
                        "probabilities": {"fast": 1.0, "balanced": 0.0},
                        "confidence": 1.0,
                    }
                },
                "usage": {"input_tokens": 1, "output_tokens": 1, "cost": 0.0},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = OpenRouterDecisionClient(
            api_key="secret",
            client=http_client,
            settings=DecisionClientSettings(max_attempts=2, retry_backoff_s=0),
        )
        result = await client.decide(
            state={"request": "x"},
            questions={
                "tier": DecisionQuestion(
                    type="choice",
                    instructions="Choose",
                    criteria={"fast": "F", "balanced": "B"},
                )
            },
        )

    assert calls == 2
    assert result.attempts == 2


@pytest.mark.asyncio
async def test_openrouter_decision_client_opens_circuit_after_failures():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "broken"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = OpenRouterDecisionClient(
            api_key="secret",
            client=http_client,
            settings=DecisionClientSettings(
                max_attempts=1, failure_limit=1, retry_backoff_s=0
            ),
        )
        questions = {
            "tier": DecisionQuestion(
                type="choice",
                instructions="Choose",
                criteria={"fast": "F", "balanced": "B"},
            )
        }
        with pytest.raises(DecisionRequestError):
            await client.decide(state={"request": "x"}, questions=questions)
        with pytest.raises(DecisionCircuitOpenError):
            await client.decide(state={"request": "x"}, questions=questions)


class _FakeDecisionProvider:
    def __init__(self, *, tier: str, confidence: float, strong: float) -> None:
        self.tier = tier
        self.confidence = confidence
        self.strong = strong
        self.states: list[dict[str, object]] = []

    async def decide(self, *, state, questions, model=None):
        from agent_driver.llm.decisions import DecisionAnswer, DecisionResponse

        self.states.append(state)
        labels = list(questions["tier"].criteria)
        other = (1.0 - self.confidence) / max(1, len(labels) - 1)
        probabilities = {
            label: self.confidence if label == self.tier else other for label in labels
        }
        response = DecisionResponse(
            model="typesafe/jev-1.13-20260917",
            model_version="typesafe/jev-1.13-20260917",
            request_id="decision-1",
            answers={
                "tier": DecisionAnswer(
                    question_id="tier",
                    type="choice",
                    choice=self.tier,
                    probabilities=probabilities,
                    confidence=self.confidence,
                ),
                "requires_strong": DecisionAnswer(
                    question_id="requires_strong", type="noul", noul=self.strong
                ),
            },
            cost_usd=0.001,
            input_tokens=10,
            output_tokens=2,
        )
        response.validate_questions(questions)
        return response


def _ctx(text: str, **kwargs: object) -> RouteContext:
    return RouteContext(
        messages=[{"role": "user", "content": text}],
        run_input=_RUN,
        default_role="balanced",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_jev_tier_router_maps_choice_and_records_safe_metadata():
    provider = _FakeDecisionProvider(tier="balanced", confidence=0.9, strong=0.1)
    router = JevTierRouter(decision_provider=provider)

    assert await router.aroute(_ctx("summarize this")) == "balanced"
    assert provider.states[0]["request"] == "summarize this"
    assert router.last_decision_metadata["source"] == "jev"
    assert (
        router.last_decision_metadata["model_version"] == "typesafe/jev-1.13-20260917"
    )
    assert "request" not in router.last_decision_metadata


@pytest.mark.asyncio
async def test_jev_tier_router_supports_custom_role_names():
    provider = _FakeDecisionProvider(tier="mid", confidence=0.9, strong=0.1)
    router = JevTierRouter(
        decision_provider=provider,
        fast_role="cheap",
        balanced_role="mid",
        strong_role="smart",
    )

    assert await router.aroute(_ctx("summarize this")) == "mid"
    assert provider.states[0]["candidate_roles"] == {
        "cheap": "fast / low-risk",
        "mid": "balanced / ordinary agent work",
        "smart": "strong / complex or high-impact work",
    }


@pytest.mark.asyncio
async def test_jev_tier_router_strong_question_overrides_tier_choice():
    provider = _FakeDecisionProvider(tier="fast", confidence=0.95, strong=0.9)
    router = JevTierRouter(decision_provider=provider)

    assert await router.aroute(_ctx("deploy this irreversible change")) == "strong"


@pytest.mark.asyncio
async def test_jev_tier_router_high_risk_floor_overrides_choice():
    provider = _FakeDecisionProvider(tier="fast", confidence=0.95, strong=0.1)
    router = JevTierRouter(decision_provider=provider)

    assert await router.aroute(_ctx("run it", risk_class="irreversible")) == "strong"


@pytest.mark.asyncio
async def test_jev_tier_router_explicit_deep_reasoning_floor_overrides_choice():
    provider = _FakeDecisionProvider(tier="fast", confidence=0.95, strong=0.1)
    router = JevTierRouter(decision_provider=provider)

    assert await router.aroute(_ctx("plan the migration steps")) == "strong"


@pytest.mark.asyncio
async def test_jev_tier_router_low_confidence_falls_back_to_heuristic():
    provider = _FakeDecisionProvider(tier="balanced", confidence=0.1, strong=0.1)
    router = JevTierRouter(decision_provider=provider)

    assert await router.aroute(_ctx("please summarize this")) == "fast"
    assert router.last_decision_metadata["source"] == "fallback"
    assert router.last_decision_metadata["fallback_reason"] == "low_confidence"
    assert router.last_decision_metadata["confidence"] == 0.1
    assert router.last_decision_metadata["cost_usd"] == 0.001
