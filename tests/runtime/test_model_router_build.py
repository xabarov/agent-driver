"""R6 — a configured model router picks the model_role at build time, composing with
R2 (model_role_map) and R3 (model_role rides LlmRequest for provider routing)."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

from agent_driver.contracts import AgentRunInput, ToolPolicyInput
from agent_driver.llm.model_router import HeuristicDifficultyRouter, PlanExecuteRouter
from agent_driver.runtime.single_agent.llm_step.build import (
    LlmRequestBuildContext,
    build_single_agent_llm_request,
)


class _EmptyRegistry:
    def list_registered(self) -> Iterator[SimpleNamespace]:
        return iter(())


class _FixedRouter:
    def __init__(self, role: str) -> None:
        self.role = role

    def route(self, ctx) -> str:
        return self.role


class _BoomRouter:
    def route(self, ctx) -> str:
        raise RuntimeError("router blew up")


def _ctx(text="hi", **kw) -> LlmRequestBuildContext:
    tool_policy = kw.pop("tool_policy", None)
    run_input = AgentRunInput(
        input=text,
        agent_id="a",
        graph_preset="single_react",
        **({"tool_policy": tool_policy} if tool_policy is not None else {}),
    )
    return LlmRequestBuildContext(
        run_input=run_input, registry=_EmptyRegistry(), max_chars=4000, max_messages=10, **kw
    )


def test_router_sets_model_role_and_resolves_model():
    req, _ = build_single_agent_llm_request(
        _ctx(model_router=_FixedRouter("strong"), model_role_map={"strong": "big"})
    )
    assert req.model_role == "strong"
    assert req.model == "big"


def test_router_role_rides_request_even_without_map():
    # No model_role_map entry → model stays None, but the routed role is on the request
    # so R3 provider routing + telemetry still see it.
    req, _ = build_single_agent_llm_request(_ctx(model_router=_FixedRouter("strong")))
    assert req.model_role == "strong"
    assert req.model is None


def test_forced_model_bypasses_router():
    tp = ToolPolicyInput(metadata={"forced_model": "forced-x"})
    req, _ = build_single_agent_llm_request(
        _ctx(
            model_router=_FixedRouter("strong"),
            model_role_map={"strong": "big"},
            tool_policy=tp,
        )
    )
    assert req.model == "forced-x"
    # role stays the run default when forced_model short-circuits routing
    assert req.model_role == "default"


def test_no_router_uses_static_model_role():
    req, _ = build_single_agent_llm_request(_ctx(model_role_map={"strong": "big"}))
    assert req.model_role == "default"
    assert req.model is None


def test_router_exception_falls_back_to_default_role():
    req, _ = build_single_agent_llm_request(
        _ctx(model_router=_BoomRouter(), model_role_map={"strong": "big"})
    )
    assert req.model_role == "default"
    assert req.model is None


def test_heuristic_router_end_to_end():
    req, _ = build_single_agent_llm_request(
        _ctx(
            "plan the big migration",
            model_router=HeuristicDifficultyRouter(),
            model_role_map={"strong": "big", "simple": "cheap"},
        )
    )
    assert req.model == "big"


def test_plan_execute_router_routes_by_step_index():
    # R5 opusplan: the planning turn (step 0) → planner model, execution turns → executor.
    router = PlanExecuteRouter()
    role_map = {"planner": "PLAN", "executor": "EXEC"}
    plan_req, _ = build_single_agent_llm_request(
        _ctx(model_router=router, model_role_map=role_map, step_index=0)
    )
    exec_req, _ = build_single_agent_llm_request(
        _ctx(model_router=router, model_role_map=role_map, step_index=1)
    )
    assert (plan_req.model_role, plan_req.model) == ("planner", "PLAN")
    assert (exec_req.model_role, exec_req.model) == ("executor", "EXEC")
