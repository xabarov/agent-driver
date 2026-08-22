"""A real, dependency-free stdio MCP client (opencode-adoption EPIC-06).

Speaks JSON-RPC 2.0 over the MCP **stdio** transport: newline-delimited JSON messages
on a subprocess's stdin/stdout. No third-party MCP SDK and no network stack — just
``asyncio`` subprocess pipes — so it runs and tests anywhere the runtime does.

Lifecycle::

    client = StdioMcpClient(config)
    await client.start()          # spawn + initialize handshake
    tools = await client.list_tools()
    out = await client.call_tool("search", {"q": "x"})
    await client.aclose()

A background reader task demultiplexes responses by JSON-RPC id to per-request futures;
server-initiated notifications (no id) are ignored. The client advertises **no**
capabilities in the handshake, so a well-behaved server never sends it sampling/
elicitation requests it must answer.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from agent_driver.tools.mcp_client.config import StdioServerConfig
from agent_driver.tools.mcp_client.errors import (
    McpProtocolError,
    McpTimeoutError,
    McpTransportError,
)


class StdioMcpClient:
    """Outward MCP client over a subprocess stdio transport."""

    def __init__(self, config: StdioServerConfig) -> None:
        self._config = config
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 0
        self._closed = False
        self._server_info: dict[str, Any] = {}
        # EPIC-06: set when the server emits ``notifications/tools/list_changed`` so a
        # host can re-discover (see ``resync_mcp_server_tools``). Created lazily on the
        # running loop.
        self._tools_changed: asyncio.Event | None = None

    @property
    def tools_changed_event(self) -> asyncio.Event:
        """An ``asyncio.Event`` set each time the server signals its tool list changed."""
        if self._tools_changed is None:
            self._tools_changed = asyncio.Event()
        return self._tools_changed

    @property
    def server_id(self) -> str:
        return self._config.server_id

    @property
    def server_info(self) -> dict[str, Any]:
        """Server ``name``/``version`` reported in the initialize result (or empty)."""
        return dict(self._server_info)

    async def start(self) -> None:
        """Spawn the server process and run the initialize handshake."""
        if self._proc is not None:
            raise McpClientAlreadyStarted()
        try:
            self._proc = await asyncio.create_subprocess_exec(
                self._config.command,
                *self._config.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._config.env,
                cwd=self._config.cwd,
            )
        except (OSError, ValueError) as exc:
            raise McpTransportError(
                f"failed to launch MCP server {self._config.server_id!r}: {exc}"
            ) from exc
        self._reader_task = asyncio.ensure_future(self._read_loop())
        await self._initialize()

    async def _initialize(self) -> None:
        result = await self._request(
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
        info = result.get("serverInfo")
        if isinstance(info, dict):
            self._server_info = info
        # Per the MCP handshake, confirm readiness before issuing further requests.
        self._notify("notifications/initialized", {})

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return the server's tool descriptors ({name, description, inputSchema})."""
        return await self._list_paginated("tools/list", "tools")

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Invoke a server tool and return its raw JSON-RPC result.

        The result carries ``content`` (a list of content blocks) and an optional
        ``isError`` flag — a tool-level error is surfaced in ``isError``/``content``,
        distinct from a protocol error (raised as :class:`McpProtocolError`).
        """
        return await self._request(
            "tools/call",
            {"name": name, "arguments": dict(arguments or {})},
            timeout=self._config.request_timeout_seconds,
        )

    async def list_resources(self) -> list[dict[str, Any]]:
        return await self._list_paginated("resources/list", "resources")

    async def read_resource(self, uri: str) -> dict[str, Any]:
        return await self._request(
            "resources/read",
            {"uri": uri},
            timeout=self._config.request_timeout_seconds,
        )

    async def _list_paginated(self, method: str, key: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        # Bound the cursor walk so a misbehaving server can't loop forever.
        for _ in range(100):
            params: dict[str, Any] = {}
            if cursor is not None:
                params["cursor"] = cursor
            result = await self._request(
                method, params, timeout=self._config.request_timeout_seconds
            )
            items = result.get(key)
            if isinstance(items, list):
                rows.extend(item for item in items if isinstance(item, dict))
            cursor = result.get("nextCursor")
            if not isinstance(cursor, str) or not cursor:
                break
        return rows

    async def _request(
        self, method: str, params: dict[str, Any], *, timeout: float
    ) -> dict[str, Any]:
        if self._closed or self._proc is None:
            raise McpTransportError("MCP client is not started or already closed")
        self._next_id += 1
        request_id = self._next_id
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future
        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        self._write_message(message)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(request_id, None)
            raise McpTimeoutError(
                f"MCP request {method!r} timed out after {timeout}s"
            ) from exc

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._write_message({"jsonrpc": "2.0", "method": method, "params": params})

    def _write_message(self, message: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise McpTransportError("MCP transport stdin is unavailable")
        try:
            proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        except (BrokenPipeError, ConnectionResetError, RuntimeError) as exc:
            raise McpTransportError(f"MCP transport write failed: {exc}") from exc

    async def _read_loop(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break  # EOF — server closed stdout
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    message = json.loads(stripped)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue  # non-JSON stderr-style noise on stdout — skip
                if isinstance(message, dict):
                    self._dispatch(message)
        finally:
            self._fail_pending(McpTransportError("MCP server stream ended"))

    def _dispatch(self, message: dict[str, Any]) -> None:
        raw_id = message.get("id")
        if not isinstance(raw_id, int):
            # notification (no id) — surface only the tool-list-changed signal.
            if message.get("method") == "notifications/tools/list_changed":
                self.tools_changed_event.set()
            return
        future = self._pending.pop(raw_id, None)
        if future is None or future.done():
            return
        if "error" in message and message["error"] is not None:
            err = message["error"]
            code = err.get("code") if isinstance(err, dict) else None
            msg = err.get("message") if isinstance(err, dict) else str(err)
            future.set_exception(
                McpProtocolError(f"MCP error: {msg}", code=code)
            )
            return
        result = message.get("result")
        future.set_result(result if isinstance(result, dict) else {})

    def _fail_pending(self, exc: Exception) -> None:
        pending = self._pending
        self._pending = {}
        for future in pending.values():
            if not future.done():
                future.set_exception(exc)

    async def aclose(self) -> None:
        """Terminate the server subprocess and release resources (idempotent)."""
        if self._closed:
            return
        self._closed = True
        proc = self._proc
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._fail_pending(McpTransportError("MCP client closed"))
        if proc is not None:
            if proc.stdin is not None and not proc.stdin.is_closing():
                try:
                    proc.stdin.close()
                except (OSError, RuntimeError):
                    pass
            if proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()

    async def __aenter__(self) -> "StdioMcpClient":
        await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()


class McpClientAlreadyStarted(McpTransportError):
    """Raised when ``start()`` is called on an already-started client."""

    def __init__(self) -> None:
        super().__init__("MCP client already started")


__all__ = ["StdioMcpClient", "McpClientAlreadyStarted"]
