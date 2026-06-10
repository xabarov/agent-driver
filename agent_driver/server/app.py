"""Starlette app factory for the OpenAI-compatible HTTP server.

Thin translator over the SDK agent: ``/v1/chat/completions`` (streaming and
non-streaming), ``/v1/models``, and ``/healthz``. Bearer auth gates the
``/v1/*`` routes. No business logic lives here — the run is driven through the
same ``agent.run`` / ``agent.stream_run`` the rest of the SDK uses.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from agent_driver.contracts.messages import ChatMessage
from agent_driver.server.auth import is_authorized
from agent_driver.server.openai import translate
from agent_driver.server.openai.schema import ChatCompletionRequest

if TYPE_CHECKING:
    from agent_driver.sdk.agent import Agent

logger = logging.getLogger(__name__)

SESSION_HEADER = "x-session-id"


class OpenAIServer:
    """Holds the agent + config and implements the OpenAI route handlers."""

    def __init__(
        self,
        agent: "Agent",
        *,
        model_id: str = "agent-driver",
        api_key: str | None = None,
    ) -> None:
        self._agent = agent
        self._model_id = model_id
        self._api_key = api_key
        # Server-side conversation memory for stateful (X-Session-Id) clients.
        self._sessions: dict[str, list[ChatMessage]] = {}

    # -- helpers -----------------------------------------------------------

    def _new_run_id(self) -> str:
        return f"run_{uuid.uuid4().hex[:12]}"

    def _parse_request(self, body: dict[str, Any]) -> ChatCompletionRequest:
        return ChatCompletionRequest.model_validate(body)

    def _build_run_input(
        self, request: ChatCompletionRequest, session_id: str | None, run_id: str
    ) -> Any:
        history = self._sessions.get(session_id, []) if session_id else []
        return translate.to_run_input(
            request,
            run_id=run_id,
            agent_id=self._agent.defaults.agent_id,
            graph_preset=self._agent.defaults.graph_preset,
            thread_id=session_id,
            history=history,
        )

    def _remember(self, session_id: str | None, run_input: Any, answer: str) -> None:
        """Persist the turn into server-side session memory (stateful mode)."""
        if not session_id:
            return
        history = list(run_input.messages)
        history.append(ChatMessage(role="assistant", content=answer or ""))
        self._sessions[session_id] = history

    # -- routes ------------------------------------------------------------

    async def chat_completions(self, request: Request) -> Any:
        if not is_authorized(
            request.headers.get("authorization"), api_key=self._api_key
        ):
            return JSONResponse(
                {"error": {"message": "invalid api key", "type": "auth"}},
                status_code=401,
            )
        try:
            body = await request.json()
        except (ValueError, json.JSONDecodeError):
            return JSONResponse(
                {"error": {"message": "invalid JSON body", "type": "bad_request"}},
                status_code=400,
            )
        try:
            parsed = self._parse_request(body)
        except ValueError as exc:
            return JSONResponse(
                {"error": {"message": str(exc), "type": "bad_request"}},
                status_code=400,
            )

        session_id = request.headers.get(SESSION_HEADER)
        run_id = self._new_run_id()
        run_input = self._build_run_input(parsed, session_id, run_id)
        created = int(time.time())

        if parsed.stream:
            return StreamingResponse(
                self._stream_chunks(run_input, session_id, created),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        output = await self._agent.run(run_input)
        self._remember(session_id, run_input, output.answer or "")
        return JSONResponse(
            translate.completion_object(output, model=self._model_id, created=created)
        )

    async def _stream_chunks(
        self, run_input: Any, session_id: str | None, created: int
    ):
        """Yield SSE frames of chat.completion.chunk, terminated by [DONE]."""
        stream = self._agent.stream_run(run_input)
        parts: list[str] = []

        def _frame(payload: dict[str, Any]) -> str:
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        yield _frame(
            translate.role_chunk(
                run_input.run_id, model=self._model_id, created=created
            )
        )
        async for delta in stream.text_deltas():
            parts.append(delta)
            yield _frame(
                translate.content_chunk(
                    run_input.run_id, delta, model=self._model_id, created=created
                )
            )
        output = await stream.final_output()
        # Some providers do not emit token deltas; fall back to the final answer.
        if not parts and output.answer:
            yield _frame(
                translate.content_chunk(
                    run_input.run_id,
                    output.answer,
                    model=self._model_id,
                    created=created,
                )
            )
        self._remember(session_id, run_input, output.answer or "")
        yield _frame(
            translate.final_chunk(output, model=self._model_id, created=created)
        )
        yield "data: [DONE]\n\n"

    async def list_models(self, request: Request) -> Any:
        if not is_authorized(
            request.headers.get("authorization"), api_key=self._api_key
        ):
            return JSONResponse(
                {"error": {"message": "invalid api key", "type": "auth"}},
                status_code=401,
            )
        return JSONResponse(
            {
                "object": "list",
                "data": [
                    {
                        "id": self._model_id,
                        "object": "model",
                        "created": 0,
                        "owned_by": "agent-driver",
                    }
                ],
            }
        )

    async def healthz(self, _request: Request) -> Any:
        return JSONResponse({"status": "ok"})


def create_app(
    agent: "Agent",
    *,
    model_id: str = "agent-driver",
    api_key: str | None = None,
    enable_mcp: bool = False,
) -> Starlette:
    """Build the Starlette app exposing ``agent`` over the OpenAI HTTP surface.

    When ``enable_mcp`` is set, the MCP Streamable-HTTP endpoint (``/mcp``,
    Phase 3) is mounted on the same app, gated by the same bearer key — so one
    server speaks both the OpenAI chat surface and MCP.
    """
    if not api_key:
        logger.warning(
            "agent-driver server: no API key configured — the server is OPEN. "
            "Set AGENT_DRIVER_SERVER_API_KEY and bind to loopback only."
        )
    server = OpenAIServer(agent, model_id=model_id, api_key=api_key)
    routes = [
        Route("/v1/chat/completions", server.chat_completions, methods=["POST"]),
        Route("/v1/models", server.list_models, methods=["GET"]),
        Route("/healthz", server.healthz, methods=["GET"]),
    ]
    if enable_mcp:
        from agent_driver.mcp_server.http import build_mcp_routes

        routes.extend(build_mcp_routes(agent, server_name=model_id, api_key=api_key))
    return Starlette(routes=routes)


__all__ = ["create_app", "OpenAIServer", "SESSION_HEADER"]
