"""A2A (Agent2Agent) server core — transport-agnostic JSON-RPC over the agent.

Exposes an :class:`Agent` to other agents over the Agent2Agent protocol: a
JSON-RPC 2.0 surface (``message/send`` / ``tasks/get`` / ``tasks/cancel``) plus
an Agent Card for discovery. The core is dependency-free and transport-agnostic
— :meth:`A2aServer.handle_request` takes a decoded JSON-RPC request and returns
the response; a transport (see :mod:`agent_driver.adapters.a2a.http`) pumps
bytes. Streaming (``message/stream``) is driven by the transport via
:meth:`A2aServer.run_task`.

Uses the canonical JSON-RPC/HTTP shapes (lowercase task states + ``role`` and
``kind`` discriminators), not the gRPC/proto enum variant.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:
    from agent_driver.sdk.agent import Agent

A2A_PROTOCOL_VERSION = "0.2.5"

_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_TASK_NOT_FOUND = -32001


def _text_from_parts(parts: Any) -> str:
    """Concatenate the text of a message's text parts."""
    out: list[str] = []
    for part in parts or []:
        if isinstance(part, dict):
            text = part.get("text")
            if isinstance(text, str) and (part.get("kind") in (None, "text")):
                out.append(text)
    return "".join(out)


def _text_message(
    text: str, *, role: str, context_id: str, task_id: str
) -> dict[str, Any]:
    return {
        "kind": "message",
        "role": role,
        "parts": [{"kind": "text", "text": text}],
        "messageId": f"msg-{uuid4().hex[:12]}",
        "contextId": context_id,
        "taskId": task_id,
    }


class A2aServer:
    """A2A surface over a single :class:`Agent` (JSON-RPC core + task store)."""

    def __init__(
        self,
        agent: "Agent",
        *,
        name: str = "agent-driver",
        description: str = "An agent-driver agent.",
        version: str = "0.1.0",
        url: str = "http://localhost:8000/a2a",
        max_tasks: int = 1024,
    ) -> None:
        self._agent = agent
        self._name = name
        self._description = description
        self._version = version
        self._url = url
        self._tasks: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
        self._max_tasks = max(1, max_tasks)

    # -- discovery ---------------------------------------------------------

    def agent_card(self) -> dict[str, Any]:
        """The Agent Card served at ``/.well-known/agent-card.json``."""
        return {
            "name": self._name,
            "description": self._description,
            "url": self._url,
            "version": self._version,
            "protocolVersion": A2A_PROTOCOL_VERSION,
            "capabilities": {"streaming": True, "pushNotifications": False},
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [
                {
                    "id": "chat",
                    "name": "Chat",
                    "description": "Send a message and receive the agent's answer.",
                    "tags": ["chat"],
                }
            ],
        }

    # -- task execution ----------------------------------------------------

    async def run_task(self, params: dict[str, Any]) -> dict[str, Any]:
        """Run one message as a task and return the completed Task object.

        Used by both ``message/send`` and the streaming transport.
        """
        message = params.get("message") or {}
        text = _text_from_parts(message.get("parts"))
        context_id = message.get("contextId") or f"ctx-{uuid4().hex[:12]}"
        task_id = f"task-{uuid4().hex[:12]}"
        user_message = {
            "kind": "message",
            "role": "user",
            "parts": [{"kind": "text", "text": text}],
            "messageId": message.get("messageId") or f"msg-{uuid4().hex[:12]}",
            "contextId": context_id,
            "taskId": task_id,
        }
        output = await self._agent.query(text, run_id=task_id)
        answer = getattr(output, "answer", None) or ""
        status_value = getattr(getattr(output, "status", None), "value", None)
        state = "completed" if status_value == "completed" else "failed"
        agent_message = _text_message(
            answer, role="agent", context_id=context_id, task_id=task_id
        )
        task = {
            "kind": "task",
            "id": task_id,
            "contextId": context_id,
            "status": {"state": state, "message": agent_message},
            "artifacts": [
                {
                    "artifactId": f"artifact-{uuid4().hex[:12]}",
                    "parts": [{"kind": "text", "text": answer}],
                }
            ],
            "history": [user_message],
        }
        self._store_task(task)
        return task

    def _store_task(self, task: dict[str, Any]) -> None:
        self._tasks[task["id"]] = task
        self._tasks.move_to_end(task["id"])
        while len(self._tasks) > self._max_tasks:
            self._tasks.popitem(last=False)

    # -- JSON-RPC ----------------------------------------------------------

    async def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Dispatch one JSON-RPC request; return the response (or ``None``)."""
        method = request.get("method")
        request_id = request.get("id")
        params = request.get("params") or {}
        if method == "message/send":
            task = await self.run_task(params)
            return _ok(request_id, task)
        if method == "tasks/get":
            task = self._tasks.get(params.get("id"))
            if task is None:
                return _err(request_id, _TASK_NOT_FOUND, "task not found")
            return _ok(request_id, task)
        if method == "tasks/cancel":
            task = self._tasks.get(params.get("id"))
            if task is None:
                return _err(request_id, _TASK_NOT_FOUND, "task not found")
            # Synchronous send tasks are already terminal; reflect cancel intent.
            task["status"] = {"state": "canceled"}
            return _ok(request_id, task)
        if "id" not in request:
            return None  # notification
        return _err(request_id, _METHOD_NOT_FOUND, f"method not found: {method!r}")


def _ok(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _err(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


__all__ = ["A2aServer", "A2A_PROTOCOL_VERSION"]
