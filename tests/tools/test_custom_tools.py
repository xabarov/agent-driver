"""Tests for ergonomic custom-tool registration helpers."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentProfile
from agent_driver.contracts.enums import ApprovalMode, SideEffectClass, ToolRisk
from agent_driver.tools import (
    ToolRegistry,
    custom_tool,
    register_custom_function,
    register_custom_tool,
    render_tool_doc,
    tool_from_function,
)


@pytest.mark.asyncio
async def test_register_custom_function_builds_schema_and_runs_handler() -> None:
    """Builder should derive args schema and register executable handler."""
    registry = ToolRegistry()

    async def summarize(topic: str, limit: int = 3) -> dict[str, object]:
        return {"summary": f"{topic}:{limit}"}

    manifest = register_custom_function(
        registry,
        summarize,
        remediation_hints=["Try a narrower topic when results are noisy."],
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.READ_ONLY,
        approval_mode=ApprovalMode.NEVER,
    )
    assert manifest.name == "summarize"
    assert manifest.args_schema is not None
    assert manifest.args_schema["properties"]["topic"]["type"] == "string"
    assert manifest.args_schema["properties"]["topic"]["description"]
    assert "topic" in manifest.args_schema["required"]
    tool = registry.get("summarize")
    assert tool is not None
    out = await tool.handler({"topic": "agent", "limit": 2})
    assert out["summary"] == "agent:2"


@pytest.mark.asyncio
async def test_custom_tool_decorator_registers_with_profile_overrides() -> None:
    """Decorator flow should preserve overrides and profile compatibility."""
    registry = ToolRegistry()

    @custom_tool(
        name="hello_tool",
        description="Say hello to one name.",
        remediation_hints=["Provide 'name' when empty output appears."],
        supported_profiles=[AgentProfile.REACT_TEXT],
        metadata={"application_tags": ["sample-app"]},
    )
    async def hello(name: str) -> dict[str, object]:
        return {"summary": f"hello {name}"}

    manifest = register_custom_tool(registry, hello)
    assert manifest.name == "hello_tool"
    assert manifest.supported_profiles == [AgentProfile.REACT_TEXT]
    assert manifest.metadata["application_tags"] == ["sample-app"]
    tool = registry.get("hello_tool")
    assert tool is not None
    out = await tool.handler({"name": "sdk"})
    assert out["summary"] == "hello sdk"


def test_tool_from_function_defaults_remediation_hints() -> None:
    """Custom tool registration should default remediation hints."""

    async def good_tool(topic: str) -> dict[str, object]:
        return {"summary": topic}

    definition = tool_from_function(good_tool)

    assert definition.manifest.remediation_hints
    assert "good_tool" in definition.manifest.remediation_hints[0]


def test_custom_tool_docs_include_arg_descriptions_and_hints() -> None:
    """Rendered docs should include generated arg descriptions and remediation hints."""
    manifest = tool_from_function(
        _doc_tool,
        remediation_hints=["Retry with a smaller batch size."],
    )
    docs = render_tool_doc(manifest.manifest, AgentProfile.REACT_TEXT)
    assert "Argument 'query'." in docs
    assert "Retry with a smaller batch size." in docs


def test_custom_tool_rejects_positional_only_parameters() -> None:
    """Builder should fail for positional-only signature parameters."""

    async def positional_only(topic, /) -> dict[str, object]:
        return {"summary": topic}

    with pytest.raises(TypeError, match="named parameters"):
        _ = tool_from_function(
            positional_only,
            remediation_hints=["Rewrite signature to named parameters."],
        )


@pytest.mark.asyncio
async def test_custom_tool_rejects_unknown_arguments() -> None:
    """Runtime wrapper should fail on unknown args to keep schema strict."""
    registry = ToolRegistry()

    async def summarize(topic: str) -> dict[str, object]:
        return {"summary": topic}

    _ = register_custom_function(
        registry,
        summarize,
        remediation_hints=["Use the correct argument names from tool docs."],
    )
    tool = registry.get("summarize")
    assert tool is not None
    with pytest.raises(ValueError, match="unknown arguments"):
        await tool.handler({"topic": "agent", "unexpected": "value"})


@pytest.mark.asyncio
async def test_custom_tool_rejects_non_object_results() -> None:
    """Custom-tool wrapper should require object-shaped handler output."""
    registry = ToolRegistry()

    async def bad_result(topic: str) -> str:
        return topic

    _ = register_custom_function(
        registry,
        bad_result,
        remediation_hints=["Return object payload with summary/details fields."],
    )
    tool = registry.get("bad_result")
    assert tool is not None
    with pytest.raises(ValueError, match="must return an object"):
        await tool.handler({"topic": "agent"})


@pytest.mark.asyncio
async def test_custom_tool_rejects_missing_required_arguments() -> None:
    """Wrapper should fail fast when required args are absent."""
    registry = ToolRegistry()

    async def summarize(topic: str) -> dict[str, object]:
        return {"summary": topic}

    _ = register_custom_function(
        registry,
        summarize,
        remediation_hints=["Provide required arguments from schema."],
    )
    tool = registry.get("summarize")
    assert tool is not None
    with pytest.raises(ValueError, match="missing required argument"):
        await tool.handler({})


async def _doc_tool(query: str) -> dict[str, object]:
    """Search docs for one query."""
    return {"summary": query}
