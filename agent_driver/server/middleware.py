"""ASGI middleware for the HTTP server: security headers.

A pure-ASGI header injector (not Starlette's ``BaseHTTPMiddleware``, which buffers
the body and would break SSE streaming). It only rewrites the response-start
headers, leaving streamed bodies untouched.
"""

from __future__ import annotations

from typing import Any

# Conservative defaults: an API server is not a browser document host, so deny
# framing and sniffing and advertise no referrer.
_SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
)


class SecurityHeadersMiddleware:
    """Inject a fixed set of security headers on every HTTP response."""

    def __init__(self, app: Any) -> None:
        self._app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        async def _send(message: Any) -> None:
            if message.get("type") == "http.response.start":
                headers = message.setdefault("headers", [])
                present = {name.lower() for name, _ in headers}
                for name, value in _SECURITY_HEADERS:
                    if name not in present:
                        headers.append((name, value))
            await send(message)

        await self._app(scope, receive, _send)


__all__ = ["SecurityHeadersMiddleware"]
