"""A2A HTTP transport — Agent Card + JSON-RPC endpoint on the ASGI stack.

Serves the transport-agnostic :class:`A2aServer` over HTTP (the ``[server]``
extra: Starlette + uvicorn): the Agent Card at
``GET /.well-known/agent-card.json`` for discovery, and a JSON-RPC endpoint
(``POST /a2a``) for ``message/send`` / ``message/stream`` / ``tasks/get`` /
``tasks/cancel``. Bearer auth (shared with the OpenAI server) gates the JSON-RPC
endpoint; the Agent Card is public (discovery). Starlette is imported lazily so
the A2A core stays dependency-free.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from agent_driver.adapters.a2a.server import A2aServer
from agent_driver.server.auth import is_authorized
from agent_driver.server.sse import sse_data

if TYPE_CHECKING:
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.routing import Route

    from agent_driver.persistence.record_store import RecordStore
    from agent_driver.sdk.agent import Agent

_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600


def _err(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


class A2aHttpTransport:
    """Agent Card + JSON-RPC routes bound to one :class:`A2aServer`."""

    def __init__(self, server: A2aServer, *, api_key: str | None = None) -> None:
        self._server = server
        self._api_key = api_key

    async def agent_card(self, _request: "Request") -> Any:
        from starlette.responses import JSONResponse

        return JSONResponse(self._server.agent_card())

    async def rpc(self, request: "Request") -> Any:
        from starlette.responses import JSONResponse, StreamingResponse

        if not is_authorized(
            request.headers.get("authorization"), api_key=self._api_key
        ):
            return JSONResponse(
                _err(None, _INVALID_REQUEST, "unauthorized"), status_code=401
            )
        try:
            body = await request.json()
        except (ValueError, json.JSONDecodeError) as exc:
            return JSONResponse(_err(None, _PARSE_ERROR, str(exc)), status_code=400)
        if not isinstance(body, dict):
            return JSONResponse(
                _err(None, _INVALID_REQUEST, "request must be a JSON object"),
                status_code=400,
            )

        if body.get("method") == "message/stream":
            return StreamingResponse(
                self._stream(body),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        response = await self._server.handle_request(body)
        if response is None:
            from starlette.responses import Response

            return Response(status_code=204)
        return JSONResponse(response)

    async def _stream(self, body: dict[str, Any]):
        """message/stream: emit the task, then a terminal status-update event."""
        request_id = body.get("id")
        params = body.get("params") or {}

        def _frame(result: dict[str, Any]) -> str:
            return sse_data({"jsonrpc": "2.0", "id": request_id, "result": result})

        task = await self._server.run_task(params)
        # First event: the task (working snapshot); then the terminal update.
        working = {**task, "status": {"state": "working"}}
        yield _frame(working)
        yield _frame(
            {
                "kind": "status-update",
                "taskId": task["id"],
                "contextId": task["contextId"],
                "status": task["status"],
                "final": True,
            }
        )


def build_a2a_routes(
    agent: "Agent",
    *,
    name: str = "agent-driver",
    description: str = "An agent-driver agent.",
    version: str = "0.1.0",
    url: str = "http://localhost:8000/a2a",
    api_key: str | None = None,
    path: str = "/a2a",
    store: "RecordStore | None" = None,
) -> list["Route"]:
    """Build the Agent Card + JSON-RPC routes for mounting into a Starlette app."""
    from starlette.routing import Route

    server = A2aServer(
        agent,
        name=name,
        description=description,
        version=version,
        url=url,
        store=store,
    )
    transport = A2aHttpTransport(server, api_key=api_key)
    return [
        Route("/.well-known/agent-card.json", transport.agent_card, methods=["GET"]),
        Route(path, transport.rpc, methods=["POST"]),
    ]


def create_a2a_app(
    agent: "Agent",
    *,
    name: str = "agent-driver",
    description: str = "An agent-driver agent.",
    version: str = "0.1.0",
    url: str = "http://localhost:8000/a2a",
    api_key: str | None = None,
    path: str = "/a2a",
    store: "RecordStore | None" = None,
) -> "Starlette":
    """Build a standalone Starlette app serving the A2A endpoints."""
    from starlette.applications import Starlette

    return Starlette(
        routes=build_a2a_routes(
            agent,
            name=name,
            description=description,
            version=version,
            url=url,
            api_key=api_key,
            path=path,
            store=store,
        )
    )


__all__ = ["A2aHttpTransport", "build_a2a_routes", "create_a2a_app"]
