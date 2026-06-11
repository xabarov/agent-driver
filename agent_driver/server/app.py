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
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from agent_driver.contracts.messages import ChatMessage
from agent_driver.persistence.record_store import InMemoryRecordStore
from agent_driver.server import errors
from agent_driver.server.auth import is_authorized
from agent_driver.server.middleware import SecurityHeadersMiddleware
from agent_driver.server.openai import translate
from agent_driver.server.openai.schema import ChatCompletionRequest, ResponsesRequest
from agent_driver.server.responses import ResponseManager, response_object
from agent_driver.server.runs import RunManager, resume_action_for
from agent_driver.server.sse import SSE_DONE, sse_data, sse_event

if TYPE_CHECKING:
    from agent_driver.persistence.record_store import RecordStore
    from agent_driver.sdk.agent import Agent

logger = logging.getLogger(__name__)

SESSION_HEADER = "x-session-id"
DEFAULT_MAX_SESSIONS = 1024
_SESSION_NS = "session"


class OpenAIServer:
    """Holds the agent + config and implements the OpenAI route handlers."""

    def __init__(
        self,
        agent: "Agent",
        *,
        model_id: str = "agent-driver",
        api_key: str | None = None,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        record_store: "RecordStore | None" = None,
    ) -> None:
        self._agent = agent
        self._model_id = model_id
        self._api_key = api_key
        # Server-side conversation memory for stateful (X-Session-Id) clients.
        # A durable record store survives restart; the default in-memory store
        # is a bounded LRU (lost on restart).
        self._store: "RecordStore" = record_store or InMemoryRecordStore(
            max_per_namespace=max_sessions
        )
        self._runs = RunManager(agent)
        self._responses = ResponseManager(agent, store=self._store)

    # -- helpers -----------------------------------------------------------

    def _new_run_id(self) -> str:
        return f"run_{uuid.uuid4().hex[:12]}"

    def _parse_request(self, body: dict[str, Any]) -> ChatCompletionRequest:
        return ChatCompletionRequest.model_validate(body)

    def _build_run_input(
        self, request: ChatCompletionRequest, session_id: str | None, run_id: str
    ) -> Any:
        history: list[ChatMessage] = []
        if session_id:
            raw = self._store.get(_SESSION_NS, session_id)
            if isinstance(raw, list):
                history = [ChatMessage.model_validate(m) for m in raw]
        return translate.to_run_input(
            request,
            run_id=run_id,
            agent_id=self._agent.defaults.agent_id,
            graph_preset=self._agent.defaults.graph_preset,
            thread_id=session_id,
            history=history,
        )

    def _remember(self, session_id: str | None, run_input: Any, answer: str) -> None:
        """Persist the turn into the session record store (stateful mode)."""
        if not session_id:
            return
        history = list(run_input.messages)
        history.append(ChatMessage(role="assistant", content=answer or ""))
        self._store.set(
            _SESSION_NS, session_id, [m.model_dump(mode="json") for m in history]
        )

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
            return sse_data(payload)

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
            output_audio = output.metadata.get("output_audio")
            if isinstance(output_audio, dict):
                yield _frame(
                    translate.audio_chunk(
                        run_input.run_id,
                        output_audio,
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
        yield SSE_DONE

    # -- responses (/v1/responses) ----------------------------------------

    async def create_response(self, request: Request) -> Any:
        if not is_authorized(
            request.headers.get("authorization"), api_key=self._api_key
        ):
            return self._unauthorized()
        try:
            parsed = ResponsesRequest.model_validate(await request.json())
        except (ValueError, json.JSONDecodeError) as exc:
            return JSONResponse(
                {"error": {"message": str(exc), "type": "bad_request"}}, status_code=400
            )
        if parsed.stream:
            return StreamingResponse(
                self._response_stream(parsed),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        try:
            record, output = await self._responses.create(parsed)
        except Exception as exc:  # noqa: BLE001 - mapped to an OpenAI error body
            logger.warning("responses run failed: %s", exc, exc_info=True)
            status, payload = errors.status_and_payload_for_exception(exc)
            return JSONResponse(payload, status_code=status)
        terminal_error = errors.status_and_payload_for_output(output)
        if terminal_error is not None:
            status, payload = terminal_error
            return JSONResponse(payload, status_code=status)
        return JSONResponse(response_object(record))

    async def _response_stream(self, parsed: ResponsesRequest):
        messages, run_input = self._responses.prepare(parsed)
        response_id = self._responses.new_response_id()
        stream = self._agent.stream_run(run_input)

        def _frame(event: str, data: dict[str, Any]) -> str:
            return sse_event(event, data)

        yield _frame(
            "response.created",
            {"response": {"id": response_id, "status": "in_progress"}},
        )
        try:
            async for delta in stream.text_deltas():
                yield _frame("response.output_text.delta", {"delta": delta})
            output = await stream.final_output()
        except Exception as exc:  # noqa: BLE001 - surfaced as an error event
            logger.warning("responses stream failed: %s", exc, exc_info=True)
            _, payload = errors.status_and_payload_for_exception(exc)
            yield _frame("error", payload)
            return
        record = self._responses.finalize(
            parsed, messages, output, response_id=response_id
        )
        yield _frame("response.completed", {"response": response_object(record)})

    async def get_response(self, request: Request) -> Any:
        if not is_authorized(
            request.headers.get("authorization"), api_key=self._api_key
        ):
            return self._unauthorized()
        record = self._responses.get(request.path_params["response_id"])
        if record is None:
            return JSONResponse(
                {"error": {"message": "unknown response", "type": "not_found"}},
                status_code=404,
            )
        return JSONResponse(response_object(record))

    async def delete_response(self, request: Request) -> Any:
        if not is_authorized(
            request.headers.get("authorization"), api_key=self._api_key
        ):
            return self._unauthorized()
        deleted = self._responses.delete(request.path_params["response_id"])
        return JSONResponse(
            {
                "id": request.path_params["response_id"],
                "object": "response.deleted",
                "deleted": deleted,
            },
            status_code=200 if deleted else 404,
        )

    # -- async runs (/v1/runs) --------------------------------------------

    def _unauthorized(self) -> Any:
        return JSONResponse(
            {"error": {"message": "invalid api key", "type": "auth"}}, status_code=401
        )

    async def create_run(self, request: Request) -> Any:
        if not is_authorized(
            request.headers.get("authorization"), api_key=self._api_key
        ):
            return self._unauthorized()
        try:
            parsed = self._parse_request(await request.json())
        except (ValueError, json.JSONDecodeError) as exc:
            return JSONResponse(
                {"error": {"message": str(exc), "type": "bad_request"}}, status_code=400
            )
        session_id = request.headers.get(SESSION_HEADER)
        messages = translate.to_chat_messages(parsed.messages)
        record = self._runs.start(messages, thread_id=session_id, model=parsed.model)
        return JSONResponse(record.public(), status_code=202)

    async def get_run(self, request: Request) -> Any:
        if not is_authorized(
            request.headers.get("authorization"), api_key=self._api_key
        ):
            return self._unauthorized()
        record = self._runs.get(request.path_params["run_id"])
        if record is None:
            return JSONResponse(
                {"error": {"message": "unknown run", "type": "not_found"}},
                status_code=404,
            )
        return JSONResponse(record.public())

    async def run_events(self, request: Request) -> Any:
        if not is_authorized(
            request.headers.get("authorization"), api_key=self._api_key
        ):
            return self._unauthorized()
        run_id = request.path_params["run_id"]
        if self._runs.get(run_id) is None:
            return JSONResponse(
                {"error": {"message": "unknown run", "type": "not_found"}},
                status_code=404,
            )

        async def _frames():
            async for event in self._runs.stream_events(run_id):
                yield sse_event(event["event"], event["data"])
            yield SSE_DONE

        return StreamingResponse(
            _frames(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    async def approve_run(self, request: Request) -> Any:
        if not is_authorized(
            request.headers.get("authorization"), api_key=self._api_key
        ):
            return self._unauthorized()
        run_id = request.path_params["run_id"]
        try:
            body = await request.json()
        except (ValueError, json.JSONDecodeError):
            body = {}
        action = resume_action_for(str(body.get("action", "approve")))
        if action is None:
            return JSONResponse(
                {"error": {"message": "invalid action", "type": "bad_request"}},
                status_code=400,
            )
        ok = await self._runs.approve(
            run_id,
            action,
            message=body.get("message"),
            edited_tool_args=body.get("edited_tool_args"),
        )
        if not ok:
            return JSONResponse(
                {"error": {"message": "run not awaiting approval", "type": "conflict"}},
                status_code=409,
            )
        return JSONResponse({"id": run_id, "ok": True})

    async def stop_run(self, request: Request) -> Any:
        if not is_authorized(
            request.headers.get("authorization"), api_key=self._api_key
        ):
            return self._unauthorized()
        run_id = request.path_params["run_id"]
        if not self._runs.stop(run_id):
            return JSONResponse(
                {
                    "error": {
                        "message": "run unknown or already done",
                        "type": "conflict",
                    }
                },
                status_code=409,
            )
        return JSONResponse({"id": run_id, "ok": True})

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
    enable_a2a: bool = False,
    max_sessions: int = DEFAULT_MAX_SESSIONS,
    cors_origins: Sequence[str] | None = None,
    record_store: "RecordStore | None" = None,
) -> Starlette:
    """Build the Starlette app exposing ``agent`` over the OpenAI HTTP surface.

    When ``enable_mcp`` is set, the MCP Streamable-HTTP endpoint (``/mcp``) is
    mounted on the same app; ``enable_a2a`` mounts the A2A Agent Card +
    JSON-RPC endpoint (``/.well-known/agent-card.json`` + ``/a2a``). Both reuse
    the same bearer key — so one server can speak OpenAI, MCP and A2A.

    ``record_store`` makes the server's keyed state (sessions, stored responses,
    A2A tasks) durable; pass a ``SqliteRecordStore`` to survive restart. Default
    is a shared bounded in-memory store.
    """
    if not api_key:
        logger.warning(
            "agent-driver server: no API key configured — the server is OPEN. "
            "Set AGENT_DRIVER_SERVER_API_KEY and bind to loopback only."
        )
    store = record_store or InMemoryRecordStore(max_per_namespace=max_sessions)
    server = OpenAIServer(
        agent,
        model_id=model_id,
        api_key=api_key,
        max_sessions=max_sessions,
        record_store=store,
    )
    routes = [
        Route("/v1/chat/completions", server.chat_completions, methods=["POST"]),
        Route("/v1/responses", server.create_response, methods=["POST"]),
        Route("/v1/responses/{response_id}", server.get_response, methods=["GET"]),
        Route(
            "/v1/responses/{response_id}", server.delete_response, methods=["DELETE"]
        ),
        Route("/v1/runs", server.create_run, methods=["POST"]),
        Route("/v1/runs/{run_id}", server.get_run, methods=["GET"]),
        Route("/v1/runs/{run_id}/events", server.run_events, methods=["GET"]),
        Route("/v1/runs/{run_id}/approval", server.approve_run, methods=["POST"]),
        Route("/v1/runs/{run_id}/stop", server.stop_run, methods=["POST"]),
        Route("/v1/models", server.list_models, methods=["GET"]),
        Route("/healthz", server.healthz, methods=["GET"]),
    ]
    if enable_mcp:
        from agent_driver.mcp_server.http import build_mcp_routes

        routes.extend(build_mcp_routes(agent, server_name=model_id, api_key=api_key))
    if enable_a2a:
        from agent_driver.adapters.a2a.http import build_a2a_routes

        routes.extend(
            build_a2a_routes(agent, name=model_id, api_key=api_key, store=store)
        )

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
