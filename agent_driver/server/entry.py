"""Entry point for serving an agent over the OpenAI-compatible HTTP server."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from agent_driver.server.app import create_app

if TYPE_CHECKING:
    from agent_driver.sdk.agent import Agent

API_KEY_ENV = "AGENT_DRIVER_SERVER_API_KEY"


def serve_http(
    agent: "Agent",
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    model_id: str = "agent-driver",
    api_key: str | None = None,
) -> None:
    """Serve ``agent`` over HTTP (blocking). Requires the ``[server]`` extra.

    ``api_key`` defaults to ``$AGENT_DRIVER_SERVER_API_KEY``; when neither is
    set the server is open and should be bound to loopback only.
    """
    import uvicorn

    resolved_key = api_key if api_key is not None else os.environ.get(API_KEY_ENV)
    app = create_app(agent, model_id=model_id, api_key=resolved_key)
    uvicorn.run(app, host=host, port=port)


__all__ = ["serve_http", "API_KEY_ENV"]
