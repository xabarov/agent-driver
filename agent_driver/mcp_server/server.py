"""Expose an agent-driver :class:`Agent` as an MCP server.

This is the inverse of the built-in MCP *client*: it lets external MCP clients
(Claude Code, Cursor, Codex, another agent) drive an agent-driver agent through
the Model Context Protocol — query it, talk to a session, read history.

The server is transport-agnostic: :meth:`AgentMcpServer.handle_request` takes a
decoded JSON-RPC request object and returns the JSON-RPC response object (or
``None`` for notifications). A transport (see :mod:`agent_driver.mcp_server.stdio`)
only has to pump bytes/lines. The implementation is dependency-free — it does
not require the optional ``mcp`` SDK — so it is fully unit-testable.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent_driver.sdk.agent import Agent

MCP_PROTOCOL_VERSION = "2024-11-05"

# JSON-RPC 2.0 error codes used by the server.
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Internal handler result; rendered into an MCP CallToolResult."""

    text: str
    structured: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class McpTool:
    """One MCP tool: its advertised schema and async handler."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[ToolResult]]

    def descriptor(self) -> dict[str, Any]:
        """MCP ``tools/list`` entry."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


class McpToolError(ValueError):
    """Raised by a handler to signal a tool-level (not protocol) error."""


class AgentMcpServer:
    """MCP server surface over a single agent-driver :class:`Agent`."""

    def __init__(
        self,
        agent: "Agent",
        *,
        server_name: str = "agent-driver",
        server_version: str = "0.1.0",
    ) -> None:
        self._agent = agent
        self._server_name = server_name
        self._server_version = server_version
        self._tools: dict[str, McpTool] = {}
        for tool in self._build_tools():
            self._tools[tool.name] = tool

    # ------------------------------------------------------------------
    # Tool surface
    # ------------------------------------------------------------------

    def _build_tools(self) -> list[McpTool]:
        return [
            McpTool(
                name="agent_query",
                description="Run a one-shot query and return the agent's answer.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "input": {"type": "string", "description": "User prompt."},
                        "run_id": {"type": "string"},
                    },
                    "required": ["input"],
                    "additionalProperties": False,
                },
                handler=self._tool_agent_query,
            ),
            McpTool(
                name="session_send",
                description="Send one turn to a named session and return the answer.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string"},
                        "input": {"type": "string"},
                    },
                    "required": ["session_id", "input"],
                    "additionalProperties": False,
                },
                handler=self._tool_session_send,
            ),
            McpTool(
                name="session_history",
                description="Return the persisted turns of a session.",
                input_schema={
                    "type": "object",
                    "properties": {"session_id": {"type": "string"}},
                    "required": ["session_id"],
                    "additionalProperties": False,
                },
                handler=self._tool_session_history,
            ),
        ]

    async def _tool_agent_query(self, args: dict[str, Any]) -> ToolResult:
        text = _required_str(args, "input")
        output = await self._agent.query(text, run_id=args.get("run_id"))
        return _output_result(output)

    async def _tool_session_send(self, args: dict[str, Any]) -> ToolResult:
        session_id = _required_str(args, "session_id")
        text = _required_str(args, "input")
        output = await self._agent.session(session_id).send(text)
        return _output_result(output)

    async def _tool_session_history(self, args: dict[str, Any]) -> ToolResult:
        session_id = _required_str(args, "session_id")
        turns = self._agent.session(session_id).history()
        rows = [
            {
                "turn_index": turn.turn_index,
                "role": getattr(turn.message.role, "value", str(turn.message.role)),
                "content": turn.message.content,
                "created_at": turn.created_at,
            }
            for turn in turns
        ]
        return ToolResult(
            text=f"{len(rows)} turn(s) in session {session_id!r}",
            structured={"session_id": session_id, "turns": rows},
        )

    # ------------------------------------------------------------------
    # MCP surface
    # ------------------------------------------------------------------

    def list_tools(self) -> list[dict[str, Any]]:
        """Return the MCP ``tools/list`` payload."""
        return [tool.descriptor() for tool in self._tools.values()]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Run a tool and return an MCP ``CallToolResult`` object."""
        tool = self._tools.get(name)
        if tool is None:
            return _tool_error(f"unknown tool: {name!r}")
        try:
            result = await tool.handler(arguments or {})
        except McpToolError as exc:
            return _tool_error(str(exc))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # Tool failures are MCP results (isError), not protocol errors, so
            # the client sees them in-band and can react.
            return _tool_error(f"{type(exc).__name__}: {exc}")
        content: dict[str, Any] = {
            "content": [{"type": "text", "text": result.text}],
            "isError": False,
        }
        if result.structured is not None:
            content["structuredContent"] = result.structured
        return content

    async def handle_request(  # pylint: disable=too-many-return-statements
        self, request: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Dispatch one JSON-RPC request; return the response (or ``None``).

        Notifications (no ``id``) return ``None``. Unknown methods return a
        JSON-RPC ``method not found`` error.
        """
        method = request.get("method")
        request_id = request.get("id")
        is_notification = "id" not in request
        if method == "initialize":
            return _ok(request_id, self._initialize_result())
        if method in ("notifications/initialized", "initialized"):
            return None
        if method == "ping":
            return _ok(request_id, {})
        if method == "tools/list":
            return _ok(request_id, {"tools": self.list_tools()})
        if method == "tools/call":
            params = request.get("params") or {}
            name = params.get("name")
            if not isinstance(name, str):
                return _err(request_id, _INVALID_PARAMS, "missing tool name")
            result = await self.call_tool(name, params.get("arguments") or {})
            return _ok(request_id, result)
        if is_notification:
            return None
        return _err(request_id, _METHOD_NOT_FOUND, f"method not found: {method!r}")

    def _initialize_result(self) -> dict[str, Any]:
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {
                "name": self._server_name,
                "version": self._server_version,
            },
        }


def _output_result(output: Any) -> ToolResult:
    answer = getattr(output, "answer", None) or ""
    return ToolResult(
        text=answer,
        structured={
            "run_id": getattr(output, "run_id", None),
            "status": getattr(getattr(output, "status", None), "value", None),
            "answer": answer,
        },
    )


def _required_str(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise McpToolError(f"{key} is required")
    return value


def _tool_error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _ok(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _err(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


__all__ = [
    "MCP_PROTOCOL_VERSION",
    "AgentMcpServer",
    "McpTool",
    "McpToolError",
    "ToolResult",
]
