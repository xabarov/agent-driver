"""Outward MCP client (opencode-adoption EPIC-06).

A real, dependency-free client that connects the agent to external MCP servers, discovers
their tools, and registers them as governed runtime tools — replacing the readonly
fixture stub in ``tools/builtin/mcp.py``. Only the **stdio** transport ships in this
slice; HTTP/SSE + OAuth are a documented follow-on.
"""

from __future__ import annotations

from agent_driver.tools.mcp_client.config import HttpServerConfig, StdioServerConfig
from agent_driver.tools.mcp_client.errors import (
    McpClientError,
    McpProtocolError,
    McpTimeoutError,
    McpTransportError,
)
from agent_driver.tools.mcp_client.http_client import HttpMcpClient
from agent_driver.tools.mcp_client.registrar import (
    McpRegistration,
    namespaced_tool_name,
    normalize_call_result,
    register_http_mcp_server,
    register_mcp_client,
    register_stdio_mcp_server,
)
from agent_driver.tools.mcp_client.stdio_client import StdioMcpClient

__all__ = [
    "HttpMcpClient",
    "HttpServerConfig",
    "McpClientError",
    "McpProtocolError",
    "McpRegistration",
    "McpTimeoutError",
    "McpTransportError",
    "StdioMcpClient",
    "StdioServerConfig",
    "namespaced_tool_name",
    "normalize_call_result",
    "register_http_mcp_server",
    "register_mcp_client",
    "register_stdio_mcp_server",
]
