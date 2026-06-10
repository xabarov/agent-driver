"""Bearer-token authentication for the HTTP server.

A single static API key, compared against the ``Authorization: Bearer <key>``
header. The key is supplied by the caller (typically from
``AGENT_DRIVER_SERVER_API_KEY``). When no key is configured the server is open;
the app factory logs a warning and is expected to bind to loopback only.
"""

from __future__ import annotations

import hmac


def extract_bearer(authorization: str | None) -> str | None:
    """Return the token from an ``Authorization: Bearer <token>`` header."""
    if not authorization:
        return None
    prefix = "bearer "
    if authorization[: len(prefix)].lower() != prefix:
        return None
    token = authorization[len(prefix) :].strip()
    return token or None


def is_authorized(authorization: str | None, *, api_key: str | None) -> bool:
    """Check a request's Authorization header against the configured key.

    Open (always authorized) when ``api_key`` is unset. Uses a constant-time
    comparison to avoid leaking the key through timing.
    """
    if not api_key:
        return True
    token = extract_bearer(authorization)
    if token is None:
        return False
    return hmac.compare_digest(token, api_key)


__all__ = ["extract_bearer", "is_authorized"]
