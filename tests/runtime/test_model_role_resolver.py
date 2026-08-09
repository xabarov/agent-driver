"""R2 — role → model resolver: the (previously inert) model_role label now resolves to
a model via CapabilitySettings.model_role_map, with forced_model taking precedence."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

from agent_driver.contracts import AgentRunInput, ToolPolicyInput
from agent_driver.runtime.single_agent.lifecycle.config_sections import (
    CapabilitySettings,
)
from agent_driver.runtime.single_agent.llm_step.build import (
    LlmRequestBuildContext,
    build_single_agent_llm_request,
)
from agent_driver.runtime.single_agent.types import RunnerConfig


class _EmptyRegistry:
    def list_registered(self) -> Iterator[SimpleNamespace]:
        return iter(())


def _ctx(*, model_role_map=None, **run_kwargs) -> LlmRequestBuildContext:
    run_input = AgentRunInput(
        input="hi", agent_id="a", graph_preset="single_react", **run_kwargs
    )
    return LlmRequestBuildContext(
        run_input=run_input,
        registry=_EmptyRegistry(),
        max_chars=4000,
        max_messages=10,
        model_role_map=model_role_map or {},
    )


# --- CapabilitySettings / RunnerConfig surface -------------------------------------


def test_capability_model_for_role():
    cs = CapabilitySettings(model_role_map={"planner": "big", "executor": "cheap"})
    assert cs.model_for_role("planner") == "big"
    assert cs.model_for_role("executor") == "cheap"
    assert cs.model_for_role("unmapped") is None
    assert cs.model_for_role(None) is None


def test_capability_defensively_copies_map():
    src = {"planner": "big"}
    cs = CapabilitySettings(model_role_map=src)
    src["planner"] = "mutated"
    assert cs.model_for_role("planner") == "big"


def test_runner_config_flat_kwarg_and_delegation():
    rc = RunnerConfig(model_role_map={"planner": "big"})
    assert rc.model_role_map == {"planner": "big"}
    assert rc.model_for_role("planner") == "big"


# --- build-time resolution ----------------------------------------------------------


def test_role_resolves_to_model():
    req, _ = build_single_agent_llm_request(
        _ctx(model_role="planner", model_role_map={"planner": "big"})
    )
    assert req.model == "big"
    assert req.model_role == "planner"  # label preserved as telemetry


def test_unmapped_role_leaves_model_none():
    req, _ = build_single_agent_llm_request(
        _ctx(model_role="default", model_role_map={"planner": "big"})
    )
    assert req.model is None


def test_empty_map_is_legacy_single_model_path():
    req, _ = build_single_agent_llm_request(_ctx(model_role="planner"))
    assert req.model is None


def test_forced_model_wins_over_role_map():
    tp = ToolPolicyInput(metadata={"forced_model": "forced-x"})
    req, _ = build_single_agent_llm_request(
        _ctx(model_role="planner", tool_policy=tp, model_role_map={"planner": "big"})
    )
    assert req.model == "forced-x"


def test_default_role_key_applies_globally():
    # An operator can pin a global default model by mapping the "default" role.
    req, _ = build_single_agent_llm_request(
        _ctx(model_role="default", model_role_map={"default": "house-model"})
    )
    assert req.model == "house-model"
