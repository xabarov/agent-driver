"""Expose an agent-driver agent as an MCP server (the inverse of the client)."""

from agent_driver.mcp_server.server import (
    MCP_PROTOCOL_VERSION,
    AgentMcpServer,
    McpTool,
    McpToolError,
    ToolResult,
)
from agent_driver.mcp_server.stdio import serve_stdio, serve_stream
from agent_driver.mcp_server.governance import (
    build_mcp_approval_decisions,
    build_mcp_call_provenance,
    build_mcp_governance_compatibility_report,
    build_mcp_governance_evidence_index,
    build_mcp_registry_snapshot,
    evaluate_mcp_approval,
    render_mcp_governance_markdown,
    replay_mcp_governance_from_artifacts,
    seed_chat_demo_mcp_governance_report,
    seed_excel_mcp_governance_report,
    write_mcp_governance_artifacts,
)

__all__ = [
    "MCP_PROTOCOL_VERSION",
    "AgentMcpServer",
    "McpTool",
    "McpToolError",
    "ToolResult",
    "serve_stdio",
    "serve_stream",
    "build_mcp_approval_decisions",
    "build_mcp_call_provenance",
    "build_mcp_governance_compatibility_report",
    "build_mcp_governance_evidence_index",
    "build_mcp_registry_snapshot",
    "evaluate_mcp_approval",
    "render_mcp_governance_markdown",
    "replay_mcp_governance_from_artifacts",
    "seed_chat_demo_mcp_governance_report",
    "seed_excel_mcp_governance_report",
    "write_mcp_governance_artifacts",
]
