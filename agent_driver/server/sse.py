"""Server-Sent Events framing helpers for the HTTP server.

One place to build the SSE wire frames the OpenAI server and the A2A HTTP
transport emit, instead of hand-rolling ``f"data: {json.dumps(...)}\\n\\n"`` at
each call site.
"""

from __future__ import annotations

import json
from typing import Any

# Terminal sentinel used by the OpenAI streaming surfaces.
SSE_DONE = "data: [DONE]\n\n"


def sse_data(payload: Any) -> str:
    """A ``data:``-only SSE frame carrying a JSON payload."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def sse_event(event: str, data: Any) -> str:
    """A named SSE frame (``event:`` + ``data:``) carrying a JSON payload."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


__all__ = ["SSE_DONE", "sse_data", "sse_event"]
