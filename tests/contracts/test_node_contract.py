"""NodeContract schema: opt-in, inert by default, plumbs through AgentRunInput."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput, FinalizeNow, NodeContract


def test_default_node_contract_is_inert() -> None:
    ri = AgentRunInput(agent_id="a", graph_preset="single_react", input="go")
    assert ri.node_contract.is_active() is False
    assert ri.node_contract.require_tool_use is False
    assert ri.node_contract.finalize_when_tools == []


def test_configured_contract_is_active_and_plumbs() -> None:
    nc = NodeContract(
        require_tool_use=True,
        require_callable_tools=True,
        target="culmen.com",
        task_hint="enumerate passive subdomains",
        finalize_when_tools=["subfinder", "ctfr"],
        max_tool_use_reprompts=2,
    )
    assert nc.is_active() is True
    ri = AgentRunInput(agent_id="a", graph_preset="single_react", input="go", node_contract=nc)
    assert ri.node_contract.target == "culmen.com"
    assert ri.node_contract.finalize_when_tools == ["subfinder", "ctfr"]
    assert ri.node_contract.max_tool_use_reprompts == 2


def test_finalize_when_tools_only_is_active() -> None:
    nc = NodeContract(finalize_when_tools=["subfinder"])
    assert nc.is_active() is True


def test_negative_reprompts_rejected() -> None:
    with pytest.raises(ValueError):
        NodeContract(max_tool_use_reprompts=-1)


def test_node_contract_json_round_trip() -> None:
    nc = NodeContract(require_tool_use=True, target="x.com")
    ri = AgentRunInput(agent_id="a", graph_preset="single_react", input="go", node_contract=nc)
    dumped = ri.model_dump(mode="json")
    assert dumped["node_contract"]["require_tool_use"] is True
    restored = AgentRunInput.model_validate(dumped)
    assert restored.node_contract.target == "x.com"


def test_finalize_now_directive() -> None:
    dirn = FinalizeNow(answer="done")
    assert dirn.answer == "done"
    assert dirn.reason == "tool_evidence_satisfies_contract"
    assert FinalizeNow(answer="x", reason="custom").reason == "custom"
