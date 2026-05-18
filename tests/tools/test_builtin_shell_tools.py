"""Tests for built-in governed shell tool."""

from __future__ import annotations

import pytest

from agent_driver.tools.builtin.shell import register_shell_tools
from agent_driver.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_bash_executes_readonly_command() -> None:
    """bash tool should execute safe command and return bounded output."""
    registry = ToolRegistry()
    register_shell_tools(registry)
    tool = registry.get("bash")
    assert tool is not None
    out = await tool.handler({"command": "echo hello"})
    assert out["exit_code"] == 0
    assert out["timed_out"] is False
    assert "hello" in out["stdout"]
    assert out["risk_level"] == "low"
    assert out["risk_category"] == "readonly"


@pytest.mark.asyncio
async def test_bash_blocks_destructive_command_pattern() -> None:
    """bash tool should reject destructive command keywords."""
    registry = ToolRegistry()
    register_shell_tools(registry)
    tool = registry.get("bash")
    assert tool is not None
    with pytest.raises(ValueError, match="destructive"):
        await tool.handler({"command": "rm -rf /tmp/demo"})


@pytest.mark.asyncio
async def test_bash_blocks_non_allowlisted_prefix() -> None:
    """bash tool should reject command prefixes outside read-only allowlist."""
    registry = ToolRegistry()
    register_shell_tools(registry)
    tool = registry.get("bash")
    assert tool is not None
    with pytest.raises(ValueError, match="allowlist"):
        await tool.handler({"command": "cat /etc/hosts"})


@pytest.mark.asyncio
async def test_bash_blocks_write_redirection() -> None:
    """bash tool should reject shell redirection and tee."""
    registry = ToolRegistry()
    register_shell_tools(registry)
    tool = registry.get("bash")
    assert tool is not None
    with pytest.raises(ValueError, match="redirection"):
        await tool.handler({"command": "echo hi > /tmp/out.txt"})


@pytest.mark.asyncio
async def test_bash_times_out_long_command() -> None:
    """bash tool should kill long command on timeout."""
    registry = ToolRegistry()
    register_shell_tools(registry)
    tool = registry.get("bash")
    assert tool is not None
    out = await tool.handler(
        {
            "command": "python3 -c \"__import__('time').sleep(0.3)\"",
            "timeout_seconds": 0.1,
        }
    )
    assert out["timed_out"] is True


@pytest.mark.asyncio
async def test_bash_allows_network_read_for_public_host() -> None:
    """bash tool should allow network read command for public host."""
    registry = ToolRegistry()
    register_shell_tools(registry)
    tool = registry.get("bash")
    assert tool is not None
    out = await tool.handler({"command": "curl https://example.com", "timeout_seconds": 5})
    assert out["risk_level"] == "medium"
    assert out["risk_category"] == "network_read"


@pytest.mark.asyncio
async def test_bash_blocks_network_read_for_localhost() -> None:
    """bash tool should reject localhost/private network targets."""
    registry = ToolRegistry()
    register_shell_tools(registry)
    tool = registry.get("bash")
    assert tool is not None
    with pytest.raises(ValueError, match="private"):
        await tool.handler({"command": "curl http://127.0.0.1:8000/health"})


@pytest.mark.asyncio
async def test_bash_blocks_write_like_git_subcommand() -> None:
    """bash tool should block git write-like subcommands."""
    registry = ToolRegistry()
    register_shell_tools(registry)
    tool = registry.get("bash")
    assert tool is not None
    with pytest.raises(ValueError, match="git command must be one of"):
        await tool.handler({"command": "git commit -m test"})


@pytest.mark.asyncio
async def test_bash_blocks_pipe_with_unknown_prefix() -> None:
    """bash tool should reject unsafe pipe command segments."""
    registry = ToolRegistry()
    register_shell_tools(registry)
    tool = registry.get("bash")
    assert tool is not None
    with pytest.raises(ValueError, match="allowlist"):
        await tool.handler({"command": "echo hello | cat"})
