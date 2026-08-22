"""Errors raised by the outward MCP client (opencode-adoption EPIC-06)."""

from __future__ import annotations


class McpClientError(Exception):
    """Base class for outward MCP client failures."""


class McpTransportError(McpClientError):
    """The transport/subprocess failed (spawn, pipe closed, decode error)."""


class McpTimeoutError(McpClientError):
    """A request did not receive a response within its deadline."""


class McpProtocolError(McpClientError):
    """The server returned a JSON-RPC error or a malformed response."""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


__all__ = [
    "McpClientError",
    "McpProtocolError",
    "McpTimeoutError",
    "McpTransportError",
]
