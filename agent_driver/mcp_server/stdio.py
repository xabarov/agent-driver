"""Newline-delimited JSON-RPC (stdio) transport for the MCP server.

MCP's stdio transport frames each JSON-RPC message as one line on stdin/stdout.
:func:`serve_stream` is the testable core (inject an async line source and a
write sink); :func:`serve_stdio` is the thin wrapper that binds it to the real
process stdin/stdout.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterable, Callable

from agent_driver.mcp_server.server import AgentMcpServer

_PARSE_ERROR = -32700


async def serve_stream(
    server: AgentMcpServer,
    lines: AsyncIterable[str],
    write: Callable[[str], None],
) -> None:
    """Drive ``server`` over an async line source, writing JSON-RPC replies.

    Blank lines are skipped. A line that is not valid JSON yields a JSON-RPC
    parse error. Responses (and only responses — notifications produce none)
    are written as single JSON lines via ``write``.
    """
    async for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            write(json.dumps(_parse_error(str(exc))))
            continue
        if not isinstance(request, dict):
            write(json.dumps(_parse_error("request must be a JSON object")))
            continue
        response = await server.handle_request(request)
        if response is not None:
            write(json.dumps(response))


async def serve_stdio(server: AgentMcpServer) -> None:  # pragma: no cover - real IO
    """Serve the MCP server over the process stdin/stdout."""

    async def _stdin_lines() -> "AsyncIterable[str]":
        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if line == "":  # EOF
                return
            yield line

    def _write(payload: str) -> None:
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()

    await serve_stream(server, _stdin_lines(), _write)


def _parse_error(message: str) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": _PARSE_ERROR, "message": f"parse error: {message}"},
    }


__all__ = ["serve_stdio", "serve_stream"]
