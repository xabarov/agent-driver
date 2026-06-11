"""MCP Streamable-HTTP transport (Phase 3).

Serves the transport-agnostic :class:`AgentMcpServer` JSON-RPC core over HTTP on
the same ASGI stack as the OpenAI server (``[server]`` extra: Starlette +
uvicorn). It implements the MCP *Streamable HTTP* transport: a single ``/mcp``
endpoint where the client POSTs JSON-RPC messages and reads the response as JSON.

- ``POST /mcp`` with a request (has ``id``) → the JSON-RPC response as
  ``application/json``. A new session id is minted on ``initialize`` and
  returned in the ``Mcp-Session-Id`` header.
- ``POST /mcp`` with only notifications/responses (no ``id``) → ``202 Accepted``.
- ``POST /mcp`` with a JSON-RPC batch (array) → an array of responses (or 202).
- ``GET /mcp`` → ``405`` (this server does not push server-initiated messages).
- ``DELETE /mcp`` → terminate the session (``204``).

Bearer auth (shared with the OpenAI server) gates the endpoint. Starlette is
imported lazily so the core MCP server stays dependency-free for stdio use.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

from agent_driver.mcp_server.server import AgentMcpServer
from agent_driver.server.auth import is_authorized

if TYPE_CHECKING:
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.routing import Route

    from agent_driver.sdk.agent import Agent

SESSION_HEADER = "mcp-session-id"
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600


def _parse_error(message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": _PARSE_ERROR, "message": f"parse error: {message}"},
    }


def _is_initialize(message: Any) -> bool:
    return isinstance(message, dict) and message.get("method") == "initialize"


class McpHttpTransport:
    """Streamable-HTTP transport bound to one :class:`AgentMcpServer`."""

    def __init__(self, server: AgentMcpServer, *, api_key: str | None = None) -> None:
        self._server = server
        self._api_key = api_key
        self._sessions: set[str] = set()

    # -- helpers -----------------------------------------------------------

    def _authorized(self, request: "Request") -> bool:
        return is_authorized(
            request.headers.get("authorization"), api_key=self._api_key
        )

    async def _dispatch(self, message: Any) -> dict[str, Any] | None:
        """Handle one JSON-RPC message, guarding against non-object inputs."""
        if not isinstance(message, dict):
            return {**_parse_error("message must be a JSON object")}
        return await self._server.handle_request(message)

    # -- routes ------------------------------------------------------------

    async def handle_post(self, request: "Request") -> Any:
        from starlette.responses import JSONResponse, Response

        if not self._authorized(request):
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": _INVALID_REQUEST, "message": "unauthorized"},
                },
                status_code=401,
            )
        try:
            body = await request.json()
        except (ValueError, json.JSONDecodeError) as exc:
            return JSONResponse(_parse_error(str(exc)), status_code=400)

        minted_session: str | None = None
        headers: dict[str, str] = {}

        # A JSON-RPC batch is an array; a single message is an object.
        if isinstance(body, list):
            responses: list[dict[str, Any]] = []
            for message in body:
                if _is_initialize(message):
                    minted_session = minted_session or self._mint_session()
                response = await self._dispatch(message)
                if response is not None:
                    responses.append(response)
            if minted_session:
                headers[SESSION_HEADER] = minted_session
            if not responses:
                return Response(status_code=202, headers=headers)
            return JSONResponse(responses, headers=headers)

        if _is_initialize(body):
            minted_session = self._mint_session()
            headers[SESSION_HEADER] = minted_session
        response = await self._dispatch(body)
        if response is None:
            return Response(status_code=202, headers=headers)
        return JSONResponse(response, headers=headers)

    async def handle_get(self, _request: "Request") -> Any:
        from starlette.responses import Response

        # No server-initiated SSE stream: the spec allows a 405 here.
        return Response(status_code=405, headers={"Allow": "POST, DELETE"})

    async def handle_delete(self, request: "Request") -> Any:
        from starlette.responses import Response

        session_id = request.headers.get(SESSION_HEADER)
        if session_id:
            self._sessions.discard(session_id)
        return Response(status_code=204)

    def _mint_session(self) -> str:
        session_id = uuid.uuid4().hex
        self._sessions.add(session_id)
        return session_id


def build_mcp_routes(
    agent: "Agent",
    *,
    server_name: str = "agent-driver",
    server_version: str = "0.1.0",
    api_key: str | None = None,
    path: str = "/mcp",
) -> list["Route"]:
    """Build the ``/mcp`` routes for mounting into an existing Starlette app."""
    from starlette.routing import Route

    server = AgentMcpServer(
        agent, server_name=server_name, server_version=server_version
    )
    transport = McpHttpTransport(server, api_key=api_key)
    return [
        Route(path, transport.handle_post, methods=["POST"]),
        Route(path, transport.handle_get, methods=["GET"]),
        Route(path, transport.handle_delete, methods=["DELETE"]),
    ]


def create_mcp_app(
    agent: "Agent",
    *,
    server_name: str = "agent-driver",
    server_version: str = "0.1.0",
    api_key: str | None = None,
    path: str = "/mcp",
) -> "Starlette":
    """Build a standalone Starlette app serving the MCP Streamable-HTTP endpoint."""
    from starlette.applications import Starlette

    return Starlette(
        routes=build_mcp_routes(
            agent,
            server_name=server_name,
            server_version=server_version,
            api_key=api_key,
            path=path,
        )
    )


__all__ = [
    "McpHttpTransport",
    "build_mcp_routes",
    "create_mcp_app",
    "SESSION_HEADER",
]
