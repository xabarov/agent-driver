"""Expose an agent-driver agent as an MCP server (the inverse of the client)."""

from agent_driver.mcp_server.server import (
    MCP_PROTOCOL_VERSION,
    AgentMcpServer,
    McpTool,
    McpToolError,
    ToolResult,
)
from agent_driver.mcp_server.stdio import serve_stdio, serve_stream

__all__ = [
    "MCP_PROTOCOL_VERSION",
    "AgentMcpServer",
    "McpTool",
    "McpToolError",
    "ToolResult",
    "serve_stdio",
    "serve_stream",
]
