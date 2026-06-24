"""Deferred tools (manifest.should_defer) are omitted from the LLM schema
enumeration but stay invocable + discoverable.

Phase 12 H21 wiring: the schema builder (_request_tools_from_registry) now skips
deferred tools so bulky/niche tool sets don't inflate every prompt, while
effective_tool_names_from_registry still lists them (invocation is gated by
evaluate_tool_policy, not the schema layer) and an explicit allowlist overrides.
"""
from agent_driver.contracts import ToolManifest
from agent_driver.runtime.single_agent.llm_step.build import (
    _request_tools_from_registry,
    effective_tool_names_from_registry,
)
from agent_driver.tools import ToolRegistry


async def _noop(_args):
    return {}


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(
        ToolManifest(name="read_x", description="d", args_schema={"type": "object", "properties": {}}),
        _noop,
    )
    reg.register(
        ToolManifest(
            name="niche_y", description="d",
            args_schema={"type": "object", "properties": {}}, should_defer=True,
        ),
        _noop,
    )
    return reg


def _schema_names(schemas):
    out = []
    for s in schemas:
        out.append(s["function"]["name"] if "function" in s else s.get("name"))
    return set(out)


def test_deferred_tool_omitted_from_schema_enumeration():
    names = _schema_names(_request_tools_from_registry(_registry(), allowed=None, denied=None))
    assert "read_x" in names
    assert "niche_y" not in names  # deferred → not shown up-front


def test_explicit_allowlist_surfaces_a_deferred_tool():
    names = _schema_names(
        _request_tools_from_registry(_registry(), allowed=("read_x", "niche_y"), denied=None)
    )
    assert {"read_x", "niche_y"} <= names  # caller explicitly asked for it


def test_deferred_tool_still_in_effective_names_for_invocation():
    # effective names back invocation/policy — deferred must remain invocable.
    assert "niche_y" in set(effective_tool_names_from_registry(_registry()))
