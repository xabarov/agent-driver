"""Opt-in live context-quality lane against OpenRouter provider."""

from __future__ import annotations

import json
import os
import re

import pytest

from agent_driver.context.compaction import sanitize_compaction_text
from agent_driver.evals import build_synthetic_context_quality_fixture
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmRequest
from agent_driver.llm.providers_impl.openai_compatible import OpenAICompatibleProvider
from tests.live_env import load_local_dotenv_for_live_tests

pytestmark = pytest.mark.live

load_local_dotenv_for_live_tests()


def _live_enabled() -> bool:
    return os.getenv("AGENT_DRIVER_RUN_LIVE_TESTS", "").strip() == "1"


def _extract_json_object(payload: str) -> dict[str, object] | None:
    text = payload.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if match is None:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


@pytest.mark.asyncio
async def test_live_openrouter_context_quality_lane_returns_parseable_json() -> None:
    """Live lane should return strict JSON fact recall payload."""
    if not _live_enabled():
        pytest.skip("live tests disabled")
    base_url = os.getenv("AGENT_DRIVER_BASE_URL")
    model = os.getenv("AGENT_DRIVER_MODEL")
    api_key = os.getenv("AGENT_DRIVER_API_KEY")
    if not base_url or not model:
        pytest.skip("live OpenRouter env is not configured")
    if not api_key:
        pytest.skip("OpenRouter API key is not configured")
    fixture = build_synthetic_context_quality_fixture()
    retained_fact_ids = [
        "fact_retrieval_window",
        "fact_openrouter_lane_optin",
        "fact_compaction_audit_keys",
    ]
    removed_fact_ids = ["fact_planning_update_channel"]
    provider = OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="openrouter-live-context-quality",
            base_url=base_url,
            api_key=api_key,
            model=model,
        )
    )
    prompt = sanitize_compaction_text(
        (
            "You are validating context retention.\n"
            f"Expected fact ids: {list(fixture.expected_fact_ids)}.\n"
            f"Retained context fact ids: {retained_fact_ids}.\n"
            f"Removed-by-trimming fact ids: {removed_fact_ids}.\n"
            "Return strict JSON only with shape:\n"
            '{"remembered": ["fact_id"], "missing": [{"fact_id": "id", "reason": "removed_or_not_used"}], "confidence": 0.0}'
        )
    )
    response = await provider.complete(
        LlmRequest(
            messages=[ChatMessage(role="user", content=prompt)],
            model=model,
            metadata={"lane": "context_quality_live"},
        )
    )
    text = response.message.content.strip()
    payload = _extract_json_object(text)
    assert isinstance(payload, dict)
    remembered = payload.get("remembered")
    missing = payload.get("missing")
    confidence = payload.get("confidence")
    assert isinstance(remembered, list)
    assert isinstance(missing, list)
    assert isinstance(confidence, (float, int))

    remembered_ids = {item for item in remembered if isinstance(item, str)}
    assert "fact_retrieval_window" in remembered_ids

    removed_or_not_used = {}
    for row in missing:
        if not isinstance(row, dict):
            continue
        fact_id = row.get("fact_id")
        reason = row.get("reason")
        if isinstance(fact_id, str) and isinstance(reason, str):
            removed_or_not_used[fact_id] = reason
    assert "fact_planning_update_channel" in removed_or_not_used
    assert removed_or_not_used["fact_planning_update_channel"] in {
        "removed_or_not_used",
        "removed",
        "not_used",
    }
