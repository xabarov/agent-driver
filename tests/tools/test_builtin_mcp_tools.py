"""Tests for built-in readonly MCP wrapper tools."""

from __future__ import annotations

import json
import pytest

from agent_driver.tools.builtin.mcp import (
    _reset_mcp_auth_state_for_tests,
    register_mcp_tools,
)
from agent_driver.tools.registry import ToolRegistry


@pytest.fixture(autouse=True)
def _reset_auth_state() -> None:
    _reset_mcp_auth_state_for_tests()


@pytest.mark.asyncio
async def test_mcp_tool_returns_descriptor_and_arguments() -> None:
    """mcp_tool should return schema/provenance for known descriptor."""
    registry = ToolRegistry()
    register_mcp_tools(registry)
    tool = registry.get("mcp_tool")
    assert tool is not None
    out = await tool.handler(
        {
            "server": "demo-docs",
            "tool_name": "search_docs",
            "arguments": {"query": "intro"},
        }
    )
    assert out["tool_name"] == "search_docs"
    assert out["server"] == "demo-docs"
    assert out["provenance"]["readonly"] is True
    assert out["descriptor_audit"]["source"] == "builtin_mcp_fixture"
    assert out["arguments"]["query"] == "intro"
    assert out["args_schema"]["type"] == "object"
    assert tool.manifest.output_schema is not None
    assert (
        tool.manifest.metadata["descriptor_provenance"]["inventory_source"]
        == "builtin static fixtures"
    )


@pytest.mark.asyncio
async def test_mcp_list_resources_filters_by_server() -> None:
    """mcp_list_resources should return only resources from one server."""
    registry = ToolRegistry()
    register_mcp_tools(registry)
    tool = registry.get("mcp_list_resources")
    assert tool is not None
    out = await tool.handler({"server": "demo-docs"})
    assert out["server"] == "demo-docs"
    assert out["resources"]
    assert all(
        item["resource_uri"].startswith("resource://") for item in out["resources"]
    )
    assert out["descriptor_audit"]["resource_count"] == len(out["resources"])


@pytest.mark.asyncio
async def test_mcp_read_resource_returns_bounded_content() -> None:
    """mcp_read_resource should return bounded content preview."""
    registry = ToolRegistry()
    register_mcp_tools(registry)
    tool = registry.get("mcp_read_resource")
    assert tool is not None
    out = await tool.handler(
        {
            "server": "demo-docs",
            "resource_uri": "resource://docs/quickstart",
            "max_chars": 64,
        }
    )
    resource = out["resource"]
    assert resource["resource_uri"] == "resource://docs/quickstart"
    assert isinstance(resource["content"], str)
    assert len(resource["content"]) <= 64


@pytest.mark.asyncio
async def test_mcp_tool_rejects_unknown_descriptor() -> None:
    """mcp_tool should fail for unknown server/tool name."""
    registry = ToolRegistry()
    register_mcp_tools(registry)
    tool = registry.get("mcp_tool")
    assert tool is not None
    with pytest.raises(ValueError, match="unknown MCP tool"):
        await tool.handler({"server": "missing", "tool_name": "none"})


@pytest.mark.asyncio
async def test_mcp_auth_configures_token_mode_with_hint() -> None:
    """mcp_auth should persist token auth in redacted form."""
    registry = ToolRegistry()
    register_mcp_tools(registry)
    tool = registry.get("mcp_auth")
    assert tool is not None
    out = await tool.handler(
        {
            "server": "demo-docs",
            "mode": "token",
            "token": "secret-token-1234",
            "scopes": ["read:docs"],
        }
    )
    auth = out["auth"]
    assert auth["server"] == "demo-docs"
    assert auth["mode"] == "token"
    assert auth["status"] == "configured"
    assert auth["token_hint"] == "se***34"
    assert auth["scopes"] == ["read:docs"]


@pytest.mark.asyncio
async def test_mcp_auth_prepares_oauth_flow() -> None:
    """mcp_auth should return pending oauth authorization payload."""
    registry = ToolRegistry()
    register_mcp_tools(registry)
    tool = registry.get("mcp_auth")
    assert tool is not None
    out = await tool.handler({"server": "demo-ops", "mode": "oauth"})
    auth = out["auth"]
    assert auth["mode"] == "oauth"
    assert auth["status"] == "pending_user_consent"
    assert auth["authorize_url"].startswith("https://auth.example/mcp/demo-ops")
    assert out["descriptor_audit"]["mode"] == "oauth"


@pytest.mark.asyncio
async def test_mcp_tools_can_load_catalog_from_json(tmp_path) -> None:
    """mcp_tool should import descriptors from external JSON catalog."""
    catalog_path = tmp_path / "mcp_catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "server": "ext-docs",
                        "tool_name": "lookup",
                        "description": "lookup docs",
                        "args_schema": {"type": "object"},
                    }
                ],
                "resources": [],
            }
        ),
        encoding="utf-8",
    )
    registry = ToolRegistry()
    register_mcp_tools(registry)
    tool = registry.get("mcp_tool")
    assert tool is not None
    out = await tool.handler(
        {
            "server": "ext-docs",
            "tool_name": "lookup",
            "catalog_json_path": str(catalog_path),
        }
    )
    assert out["tool_name"] == "lookup"
    assert out["descriptor_audit"]["source"].startswith("catalog_json:")


@pytest.mark.asyncio
async def test_mcp_tool_allowlist_blocks_not_allowed_tool() -> None:
    """mcp_tool should enforce explicit allowlist when provided."""
    registry = ToolRegistry()
    register_mcp_tools(registry)
    tool = registry.get("mcp_tool")
    assert tool is not None
    with pytest.raises(ValueError, match="not in allowlist"):
        await tool.handler(
            {
                "server": "demo-docs",
                "tool_name": "search_docs",
                "tool_allowlist": ["list_jobs"],
            }
        )
