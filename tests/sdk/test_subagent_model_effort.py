"""R4 — per-subagent model / model_role / reasoning-effort, composing R1/R2/R3.

Asserts on the child's captured ``LlmRequest``: a subagent can run on its own model
(explicit pin or via the parent's role registries), its own effort tier, and — through
the parent runner's R3 ``role_providers`` — its own provider.
"""

from __future__ import annotations

import pytest

from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.runtime import RunStatus
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime.single_agent.types import RunnerConfig
from agent_driver.sdk import ToolSet, create_agent
from agent_driver.sdk.subagent import SubagentSpec, run_subagent


class _CapturingProvider(FakeProvider):
    def __init__(self, name: str = "fake") -> None:
        super().__init__(response_text="ok")
        self._pname = name
        self.requests: list[LlmRequest] = []

    @property
    def name(self) -> str:
        return self._pname

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return LlmResponse(
            message=ChatMessage(role="assistant", content="ok"),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider=self._pname, model_name="test"),
            provider=self._pname,
            model="test",
            metadata={},
        )


async def _run(spec, *, config=None):
    provider = _CapturingProvider("default")
    parent = create_agent(provider=provider, tools=ToolSet.only(), config=config)
    result = await run_subagent(parent, spec)
    assert result.status == RunStatus.COMPLETED
    return provider


# --- model_role default + effort + explicit model ----------------------------------


@pytest.mark.asyncio
async def test_child_model_role_defaults_to_agent_type():
    provider = await _run(SubagentSpec(agent_type="researcher", prompt="go"))
    assert provider.requests[0].model_role == "researcher"


@pytest.mark.asyncio
async def test_explicit_model_role_overrides_agent_type():
    provider = await _run(
        SubagentSpec(agent_type="researcher", prompt="go", model_role="cheap-role")
    )
    assert provider.requests[0].model_role == "cheap-role"


@pytest.mark.asyncio
async def test_reasoning_effort_flows_to_child_request():
    provider = await _run(
        SubagentSpec(agent_type="worker", prompt="go", reasoning_effort="low")
    )
    assert provider.requests[0].reasoning == {"effort": "low"}


@pytest.mark.asyncio
async def test_no_effort_leaves_reasoning_none():
    provider = await _run(SubagentSpec(agent_type="worker", prompt="go"))
    assert provider.requests[0].reasoning is None


@pytest.mark.asyncio
async def test_explicit_model_pins_child_model():
    provider = await _run(
        SubagentSpec(agent_type="worker", prompt="go", model="big-model")
    )
    assert provider.requests[0].model == "big-model"


@pytest.mark.asyncio
async def test_inherit_sentinel_does_not_pin_model():
    provider = await _run(
        SubagentSpec(agent_type="worker", prompt="go", model="inherit")
    )
    assert provider.requests[0].model is None


# --- composition with R2 (model_role_map) and R3 (role_providers) ------------------


@pytest.mark.asyncio
async def test_child_routes_to_role_model_map_by_agent_type():
    # R2: parent maps the child's agent_type → a model; the child (model_role=agent_type)
    # resolves to it without an explicit spec.model.
    config = RunnerConfig(model_role_map={"analyst": "analyst-model"})
    provider = await _run(SubagentSpec(agent_type="analyst", prompt="go"), config=config)
    assert provider.requests[0].model == "analyst-model"


@pytest.mark.asyncio
async def test_child_routes_to_role_provider_by_agent_type():
    # R3: parent binds the child's agent_type → a different provider; the child's call
    # lands on THAT provider, not the default.
    worker = _CapturingProvider("worker-provider")
    default = _CapturingProvider("default")
    config = RunnerConfig(role_providers={"worker": worker})
    parent = create_agent(provider=default, tools=ToolSet.only(), config=config)
    result = await run_subagent(parent, SubagentSpec(agent_type="worker", prompt="go"))
    assert result.status == RunStatus.COMPLETED
    assert worker.requests, "child should route to the role-bound provider"
    assert not default.requests, "default provider should not be called"


@pytest.mark.asyncio
async def test_spec_model_wins_over_role_model_map():
    config = RunnerConfig(model_role_map={"analyst": "map-model"})
    provider = await _run(
        SubagentSpec(agent_type="analyst", prompt="go", model="spec-model"),
        config=config,
    )
    assert provider.requests[0].model == "spec-model"
