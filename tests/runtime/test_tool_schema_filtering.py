"""Tests for ``ToolPolicyInput.allowed_tools`` / ``denied_tools`` filtering
at the LLM-request build layer.

The runtime policy evaluator (``tools/policy/evaluator.py``) already
denies a call when the tool isn't in ``allowed_tools`` or is in
``denied_tools``. But that gate fires AFTER the model has emitted a
``tool_use`` block — wasting an LLM round-trip and producing a noisy
denied trace. The fix is to filter at request-build time so the model
NEVER SEES the forbidden tools in its schema.

OpenClaude pattern: see ``src/tools/AgentTool/agentToolUtils.ts``'s
``resolveAgentTools`` / ``filterToolsForAgent``. Agent-driver mirrors
this for plan-mode focused-retry (excel_ai task #64 args-quality fix:
restrict registry to data tools only, then ``tool_choice="required"``).
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

from agent_driver.contracts import AgentRunInput, ToolPolicyInput
from agent_driver.contracts.enums import ToolPolicyMode
from agent_driver.contracts.tools import ToolManifest
from agent_driver.runtime.single_agent.llm import (
    LlmRequestBuildContext,
    _provider_compatible_json_schema,
    _request_tools_from_registry,
    build_single_agent_llm_request,
)


def _manifest(name: str) -> ToolManifest:
    return ToolManifest(name=name, description=f"{name} tool")


class _FakeRegistry:
    """Duck-typed ToolRegistry exposing a ``list_registered`` iterator.

    The real registry returns rows with ``manifest`` + ``handler``; the
    LLM-request builder only reads ``item.manifest`` so a SimpleNamespace
    is enough.
    """

    def __init__(self, names: list[str]) -> None:
        self._names = names

    def list_registered(self) -> Iterator[SimpleNamespace]:
        for name in self._names:
            yield SimpleNamespace(manifest=_manifest(name))


# ---------------------------------------------------------------------------
# Unit tests for _request_tools_from_registry
# ---------------------------------------------------------------------------


def test_no_filters_returns_all_registry_tools() -> None:
    registry = _FakeRegistry(["a", "b", "c"])
    tools = _request_tools_from_registry(registry)
    names = [t["function"]["name"] for t in tools]
    assert names == ["a", "b", "c"]


def test_allowed_tuple_filters_to_subset() -> None:
    """Only tools named in ``allowed`` survive."""
    registry = _FakeRegistry(["sandbox", "chart", "find", "read"])
    tools = _request_tools_from_registry(registry, allowed=("sandbox", "find", "read"))
    names = [t["function"]["name"] for t in tools]
    assert names == ["sandbox", "find", "read"]


def test_empty_allowed_tuple_returns_no_tools() -> None:
    """``allowed=()`` is the explicit "no tools" path — different from
    ``allowed=None`` (no filter). Useful for runs that want NO_TOOLS
    behaviour without flipping the policy mode."""
    registry = _FakeRegistry(["a", "b"])
    tools = _request_tools_from_registry(registry, allowed=())
    assert tools == []


def test_denied_tuple_strips_named_tools() -> None:
    registry = _FakeRegistry(["sandbox", "todo_write", "find"])
    tools = _request_tools_from_registry(registry, denied=("todo_write",))
    names = [t["function"]["name"] for t in tools]
    assert names == ["sandbox", "find"]


def test_denied_overrides_allowed_on_collision() -> None:
    """Deny wins. If a tool appears in both lists, the model must not
    see it. Prevents accidental misconfiguration leaking forbidden
    tools through a permissive allowlist."""
    registry = _FakeRegistry(["sandbox", "todo_write"])
    tools = _request_tools_from_registry(
        registry,
        allowed=("sandbox", "todo_write"),
        denied=("todo_write",),
    )
    names = [t["function"]["name"] for t in tools]
    assert names == ["sandbox"]


def test_registry_none_returns_empty_list() -> None:
    assert _request_tools_from_registry(None) == []


def test_registry_without_list_registered_returns_empty() -> None:
    """A registry-like object lacking ``list_registered`` (older test
    doubles) degrades gracefully — never raises."""
    assert _request_tools_from_registry(object()) == []  # type: ignore[arg-type]


def test_tool_schema_adds_items_to_array_properties_without_mutating_manifest() -> None:
    """OpenRouter/OpenAI tool schemas require explicit array ``items``."""
    raw = {
        "type": "object",
        "properties": {
            "mock_results": {"type": "array"},
            "nested": {
                "type": "object",
                "properties": {"rows": {"type": "array"}},
            },
        },
    }

    normalized = _provider_compatible_json_schema(raw)

    assert normalized["properties"]["mock_results"]["items"] == {}
    assert normalized["properties"]["nested"]["properties"]["rows"]["items"] == {}
    assert "items" not in raw["properties"]["mock_results"]


# ---------------------------------------------------------------------------
# Integration: ToolPolicyInput → LlmRequest.tools
# ---------------------------------------------------------------------------


def _make_build_ctx(
    registry: _FakeRegistry,
    *,
    allowed: list[str] | None = None,
    denied: list[str] | None = None,
) -> LlmRequestBuildContext:
    run_input = AgentRunInput(
        input="hello",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(
            mode=ToolPolicyMode.ALLOW_TOOLS,
            allowed_tools=allowed,
            denied_tools=denied,
        ),
    )
    return LlmRequestBuildContext(
        run_input=run_input,
        clarification=None,
        tool_docs=None,
        authorized_imports=tuple(),
        registry=registry,
        observations=tuple(),
        planning_prompt=None,
        digest_ids=tuple(),
        artifact_ids=tuple(),
        max_chars=4000,
        max_messages=10,
        max_observations=None,
        context_window_estimate=12000,
        warning_threshold=7500,
        compact_threshold=9000,
        blocking_threshold=10500,
        output_token_reserve=1500,
        stream=False,
        system_instruction=None,
        protocol_messages=None,
        tool_choice=None,
    )


def test_tool_policy_allowlist_flows_to_llm_request_tools() -> None:
    """End-to-end: ``ToolPolicyInput.allowed_tools=['sandbox', 'find']``
    on ``AgentRunInput`` → ``LlmRequest.tools`` contains exactly those
    two schemas. The model can't even attempt to call other tools."""
    registry = _FakeRegistry(
        ["sandbox", "find", "todo_write", "enter_plan_mode", "chart"]
    )
    ctx = _make_build_ctx(registry, allowed=["sandbox", "find"])
    request, _ = build_single_agent_llm_request(ctx)
    names = [t["function"]["name"] for t in request.tools]
    assert names == ["sandbox", "find"]


def test_tool_policy_denylist_flows_to_llm_request_tools() -> None:
    """Symmetric: ``denied_tools`` strips the named tool from the
    schema. The plan-retry use case: ``denied_tools=['todo_write',
    'enter_plan_mode']`` keeps the model from recursively re-planning."""
    registry = _FakeRegistry(["sandbox", "todo_write", "enter_plan_mode"])
    ctx = _make_build_ctx(registry, denied=["todo_write", "enter_plan_mode"])
    request, _ = build_single_agent_llm_request(ctx)
    names = [t["function"]["name"] for t in request.tools]
    assert names == ["sandbox"]


def test_tool_policy_unrestricted_keeps_legacy_behaviour() -> None:
    """No allowlist + no denylist → all registry tools surface. Existing
    callers without policy lists must see no behaviour change."""
    registry = _FakeRegistry(["sandbox", "find", "todo_write"])
    ctx = _make_build_ctx(registry)
    request, _ = build_single_agent_llm_request(ctx)
    names = [t["function"]["name"] for t in request.tools]
    assert names == ["sandbox", "find", "todo_write"]


def test_tool_policy_empty_allowlist_yields_zero_tools_in_request() -> None:
    """``allowed_tools=[]`` is the "explicit no tools" path through the
    schema layer. The runtime ``NO_TOOLS`` mode is a separate switch
    on the policy enum — this path lets callers achieve the same
    visible effect without flipping the mode."""
    registry = _FakeRegistry(["sandbox", "find"])
    ctx = _make_build_ctx(registry, allowed=[])
    request, _ = build_single_agent_llm_request(ctx)
    assert request.tools == []
