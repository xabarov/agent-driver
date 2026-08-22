"""A streamable-HTTP MCP client (opencode-adoption EPIC-06 follow-on).

Speaks JSON-RPC 2.0 over the MCP **streamable-HTTP** transport: each request is an HTTP
POST of one JSON-RPC message to the server's single MCP endpoint; the server answers with
either ``application/json`` (one response) or ``text/event-stream`` (SSE-framed messages).
The initialize handshake returns an ``Mcp-Session-Id`` header that the client echoes on
every subsequent request, plus the negotiated ``MCP-Protocol-Version``.

Same public surface as :class:`~agent_driver.tools.mcp_client.stdio_client.StdioMcpClient`
(``start`` / ``list_tools`` / ``call_tool`` / ``list_resources`` / ``read_resource`` /
``aclose``), so the registrar drives either transport identically. We advertise **no**
client capabilities, so responses are one-JSON-RPC-message-per-POST — no long-lived
server→client stream is opened. Uses ``httpx`` (already a project dependency).
"""

from __future__ import annotations

import json
from typing import Any

from agent_driver.tools.mcp_client.config import HttpServerConfig
from agent_driver.tools.mcp_client.errors import (
    McpProtocolError,
    McpTimeoutError,
    McpTransportError,
)


def _parse_sse_messages(body: str) -> list[dict[str, Any]]:
    """Extract JSON-RPC objects from an SSE (text/event-stream) response body."""
    messages: list[dict[str, Any]] = []
    # Events are separated by a blank line; a event's payload is its ``data:`` lines.
    for block in body.replace("\r\n", "\n").split("\n\n"):
        data_lines = [
            line[len("data:") :].lstrip()
            for line in block.split("\n")
            if line.startswith("data:")
        ]
        if not data_lines:
            continue
        try:
            parsed = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            messages.append(parsed)
    return messages


class HttpMcpClient:
    """Outward MCP client over the streamable-HTTP transport."""

    def __init__(self, config: HttpServerConfig, *, httpx_client: Any = None) -> None:
        self._config = config
        # ``httpx_client`` is a test seam: pass an ``httpx.AsyncClient`` (e.g. backed by a
        # ``MockTransport``) to drive the client without a live server. Production leaves
        # it None and ``start()`` builds the real client.
        self._client: Any = httpx_client
        self._session_id: str | None = None
        self._initialized = False
        self._next_id = 0
        self._server_info: dict[str, Any] = {}
        self._closed = False

    @property
    def server_id(self) -> str:
        return self._config.server_id

    @property
    def server_info(self) -> dict[str, Any]:
        return dict(self._server_info)

    async def start(self) -> None:
        """Open the HTTP client (unless one was injected) and run the handshake."""
        if self._initialized:
            raise McpTransportError("HTTP MCP client already started")
        if self._client is None:
            try:
                import httpx  # noqa: PLC0415
            except ImportError as exc:  # pragma: no cover - httpx is a hard dep
                raise McpTransportError(
                    "httpx is required for the HTTP MCP client"
                ) from exc
            self._client = httpx.AsyncClient(
                headers=dict(self._config.headers or {}),
                verify=self._config.verify_tls,
            )
        await self._initialize()

    async def _initialize(self) -> None:
        result, headers = await self._post_request(
            "initialize",
            {
                "protocolVersion": self._config.protocol_version,
                "capabilities": {},
                "clientInfo": {
                    "name": self._config.client_name,
                    "version": self._config.client_version,
                },
            },
            timeout=self._config.init_timeout_seconds,
        )
        session_id = headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id
        info = result.get("serverInfo")
        if isinstance(info, dict):
            self._server_info = info
        self._initialized = True
        await self._notify("notifications/initialized", {})

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self._initialized:
            headers["MCP-Protocol-Version"] = self._config.protocol_version
        return headers

    async def list_tools(self) -> list[dict[str, Any]]:
        return await self._list_paginated("tools/list", "tools")

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        result, _ = await self._post_request(
            "tools/call",
            {"name": name, "arguments": dict(arguments or {})},
            timeout=self._config.request_timeout_seconds,
        )
        return result

    async def list_resources(self) -> list[dict[str, Any]]:
        return await self._list_paginated("resources/list", "resources")

    async def read_resource(self, uri: str) -> dict[str, Any]:
        result, _ = await self._post_request(
            "resources/read",
            {"uri": uri},
            timeout=self._config.request_timeout_seconds,
        )
        return result

    async def _list_paginated(self, method: str, key: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(100):
            params: dict[str, Any] = {}
            if cursor is not None:
                params["cursor"] = cursor
            result, _headers = await self._post_request(
                method, params, timeout=self._config.request_timeout_seconds
            )
            items = result.get(key)
            if isinstance(items, list):
                rows.extend(item for item in items if isinstance(item, dict))
            cursor = result.get("nextCursor")
            if not isinstance(cursor, str) or not cursor:
                break
        return rows

    async def _post_request(
        self, method: str, params: dict[str, Any], *, timeout: float
    ) -> tuple[dict[str, Any], Any]:
        if self._closed or self._client is None:
            raise McpTransportError("HTTP MCP client is not started or already closed")
        import httpx  # noqa: PLC0415

        self._next_id += 1
        request_id = self._next_id
        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        try:
            response = await self._client.post(
                self._config.url,
                json=message,
                headers=self._headers(),
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise McpTimeoutError(
                f"MCP HTTP request {method!r} timed out after {timeout}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise McpTransportError(f"MCP HTTP request failed: {exc}") from exc
        if response.status_code >= 400:
            raise McpTransportError(
                f"MCP HTTP {response.status_code} for {method!r}: "
                f"{response.text[:200]}"
            )
        return self._extract_result(response, request_id), response.headers

    def _extract_result(self, response: Any, request_id: int) -> dict[str, Any]:
        content_type = str(response.headers.get("content-type", "")).lower()
        if "text/event-stream" in content_type:
            messages = _parse_sse_messages(response.text)
        else:
            body = response.text.strip()
            if not body:
                return {}
            try:
                parsed = response.json()
            except (json.JSONDecodeError, ValueError) as exc:
                raise McpProtocolError("non-JSON MCP response") from exc
            messages = [parsed] if isinstance(parsed, dict) else []
        match = next(
            (m for m in messages if m.get("id") == request_id),
            None,
        ) or next(
            (m for m in messages if "result" in m or "error" in m),
            None,
        )
        if match is None:
            raise McpProtocolError("no JSON-RPC response in MCP reply")
        error = match.get("error")
        if error is not None:
            code = error.get("code") if isinstance(error, dict) else None
            msg = error.get("message") if isinstance(error, dict) else str(error)
            raise McpProtocolError(f"MCP error: {msg}", code=code)
        result = match.get("result")
        return result if isinstance(result, dict) else {}

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        if self._client is None:
            return
        import httpx  # noqa: PLC0415

        message = {"jsonrpc": "2.0", "method": method, "params": params}
        try:
            await self._client.post(
                self._config.url,
                json=message,
                headers=self._headers(),
                timeout=self._config.request_timeout_seconds,
            )
        except httpx.HTTPError:
            # A notification has no response to await; a transport hiccup here is
            # non-fatal to the session.
            pass

    async def aclose(self) -> None:
        """Close the HTTP client (idempotent)."""
        if self._closed:
            return
        self._closed = True
        if self._client is not None:
            await self._client.aclose()

    async def __aenter__(self) -> "HttpMcpClient":
        await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()


__all__ = ["HttpMcpClient"]
