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
from collections import OrderedDict
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from agent_driver.contracts.messages import ChatMessage
from agent_driver.server import errors
from agent_driver.server.auth import is_authorized
from agent_driver.server.middleware import SecurityHeadersMiddleware
from agent_driver.server.openai import translate
from agent_driver.server.openai.schema import ChatCompletionRequest

if TYPE_CHECKING:
    from agent_driver.sdk.agent import Agent

logger = logging.getLogger(__name__)

SESSION_HEADER = "x-session-id"
DEFAULT_MAX_SESSIONS = 1024


class OpenAIServer:
    """Holds the agent + config and implements the OpenAI route handlers."""

    def __init__(
        self,
        agent: "Agent",
        *,
        model_id: str = "agent-driver",
        api_key: str | None = None,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
    ) -> None:
        self._agent = agent
        self._model_id = model_id
        self._api_key = api_key
        self._max_sessions = max(1, max_sessions)
        # Server-side conversation memory for stateful (X-Session-Id) clients,
        # bounded LRU so a long-lived server cannot leak memory on session ids.
        self._sessions: "OrderedDict[str, list[ChatMessage]]" = OrderedDict()

    # -- helpers -----------------------------------------------------------

    def _new_run_id(self) -> str:
        return f"run_{uuid.uuid4().hex[:12]}"

    def _parse_request(self, body: dict[str, Any]) -> ChatCompletionRequest:
        return ChatCompletionRequest.model_validate(body)

    def _build_run_input(
        self, request: ChatCompletionRequest, session_id: str | None, run_id: str
    ) -> Any:
        history: list[ChatMessage] = []
        if session_id and session_id in self._sessions:
            self._sessions.move_to_end(session_id)  # LRU touch
            history = self._sessions[session_id]
        return translate.to_run_input(
            request,
            run_id=run_id,
            agent_id=self._agent.defaults.agent_id,
            graph_preset=self._agent.defaults.graph_preset,
            thread_id=session_id,
            history=history,
        )

    def _remember(self, session_id: str | None, run_input: Any, answer: str) -> None:
        """Persist the turn into server-side session memory (stateful mode).

        Bounded LRU: the most-recently-used session is kept at the end; when the
        map exceeds ``max_sessions`` the least-recently-used entry is evicted.
        """
        if not session_id:
            return
        history = list(run_input.messages)
        history.append(ChatMessage(role="assistant", content=answer or ""))
        self._sessions[session_id] = history
        self._sessions.move_to_end(session_id)
        while len(self._sessions) > self._max_sessions:
            self._sessions.popitem(last=False)

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
                self._stream_chunks(parsed, run_input, session_id, created),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        try:
            output = await self._agent.run(run_input)
        except Exception as exc:  # noqa: BLE001 - mapped to an OpenAI error body
            logger.warning("chat.completions run failed: %s", exc, exc_info=True)
            status, payload = errors.status_and_payload_for_exception(exc)
            return JSONResponse(payload, status_code=status)
        # A non-completed terminal run (failed/timed-out) is an error, not an
        # empty completion — surface it with the right status + OpenAI envelope.
        terminal_error = errors.status_and_payload_for_output(output)
        if terminal_error is not None:
            status, payload = terminal_error
            return JSONResponse(payload, status_code=status)
        self._remember(session_id, run_input, output.answer or "")
        return JSONResponse(
            translate.completion_object(output, model=self._model_id, created=created)
        )

    async def _stream_chunks(
        self,
        parsed: ChatCompletionRequest,
        run_input: Any,
        session_id: str | None,
        created: int,
    ):
        """Yield SSE frames of chat.completion.chunk, terminated by [DONE].

        On client disconnect the generator is closed and the ``finally`` aborts
        the underlying run so it does not keep burning tokens. Mid-stream errors
        are surfaced as a trailing ``{"error": ...}`` frame before ``[DONE]``.
        """
        stream = self._agent.stream_run(run_input)
        parts: list[str] = []

        def _frame(payload: dict[str, Any]) -> str:
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        try:
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
            # Some providers do not emit token deltas; fall back to the answer.
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
            if parsed.wants_usage_chunk():
                yield _frame(
                    translate.usage_chunk(output, model=self._model_id, created=created)
                )
        except Exception as exc:  # noqa: BLE001 - surfaced as an SSE error frame
            logger.warning("chat.completions stream failed: %s", exc, exc_info=True)
            _, payload = errors.status_and_payload_for_exception(exc)
            yield _frame(payload)
        finally:
            # Abort the run if the consumer stopped early (e.g. client
            # disconnected) so it doesn't keep running detached.
            stream.cancel(reason="client_disconnect")
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
    max_sessions: int = DEFAULT_MAX_SESSIONS,
    cors_origins: Sequence[str] | None = None,
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
    server = OpenAIServer(
        agent, model_id=model_id, api_key=api_key, max_sessions=max_sessions
    )
    routes = [
        Route("/v1/chat/completions", server.chat_completions, methods=["POST"]),
        Route("/v1/models", server.list_models, methods=["GET"]),
        Route("/healthz", server.healthz, methods=["GET"]),
    ]
    if enable_mcp:
        from agent_driver.mcp_server.http import build_mcp_routes

        routes.extend(build_mcp_routes(agent, server_name=model_id, api_key=api_key))

    # Security headers on every response; CORS only when origins are configured
    # (browser clients like Open WebUI / LibreChat need it, CLI/SDK clients
    # don't). CORSMiddleware runs outermost so preflight OPTIONS short-circuits.
    middleware = [Middleware(SecurityHeadersMiddleware)]
    if cors_origins:
        middleware.insert(
            0,
            Middleware(
                CORSMiddleware,
                allow_origins=list(cors_origins),
                allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
                allow_headers=["*"],
                allow_credentials=False,
            ),
        )
    return Starlette(routes=routes, middleware=middleware)


__all__ = ["create_app", "OpenAIServer", "SESSION_HEADER"]
