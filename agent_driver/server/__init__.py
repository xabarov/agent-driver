"""OpenAI-compatible HTTP/SSE server adapter.

Importing this package requires the optional ``[server]`` dependencies
(``starlette`` + ``uvicorn``); install ``agent-driver[server]``. The core import
graph never pulls them in — only code that opts into the HTTP server imports
this package.
"""

from __future__ import annotations

from agent_driver.server.app import create_app
from agent_driver.server.entry import serve_http

__all__ = ["create_app", "serve_http"]
