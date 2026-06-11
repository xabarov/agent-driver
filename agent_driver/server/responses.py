"""OpenAI Responses API (``/v1/responses``) — stateful, chainable responses.

A thin layer over ``agent.run`` that adds the Responses-API shape: a single
``input`` (string or message items) + ``instructions`` (system), an
``output``/``output_text`` response object, and **stateful chaining** — when
``store`` is set the conversation is kept under the response id so a later
request can continue it via ``previous_response_id``. The store is a bounded LRU.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.persistence.record_store import InMemoryRecordStore
from agent_driver.server.usage import responses_usage

if TYPE_CHECKING:
    from agent_driver.contracts.runtime import AgentRunOutput
    from agent_driver.persistence.record_store import RecordStore
    from agent_driver.sdk.agent import Agent
    from agent_driver.server.openai.schema import ResponsesRequest

_RESPONSE_NS = "response"


@dataclass
class ResponseRecord:
    """A created (and optionally stored) response."""

    id: str
    created: int
    model: str
    status: str
    output_text: str = ""
    usage: dict[str, int] | None = None
    # Full conversation (incl. this turn's assistant answer) for chaining.
    messages: list[ChatMessage] = field(default_factory=list)


def _record_to_json(record: ResponseRecord) -> dict[str, Any]:
    """Serialize a record to a JSON-able dict for the record store."""
    return {
        "id": record.id,
        "created": record.created,
        "model": record.model,
        "status": record.status,
        "output_text": record.output_text,
        "usage": record.usage,
        "messages": [m.model_dump(mode="json") for m in record.messages],
    }


def _record_from_json(raw: dict[str, Any]) -> ResponseRecord:
    """Rebuild a record from its stored JSON dict."""
    return ResponseRecord(
        id=raw["id"],
        created=raw["created"],
        model=raw["model"],
        status=raw["status"],
        output_text=raw.get("output_text", ""),
        usage=raw.get("usage"),
        messages=[ChatMessage.model_validate(m) for m in raw.get("messages", [])],
    )


def response_object(record: ResponseRecord) -> dict[str, Any]:
    """Assemble the OpenAI ``response`` object for a record."""
    return {
        "id": record.id,
        "object": "response",
        "created_at": record.created,
        "model": record.model,
        "status": record.status,
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": record.output_text or ""}],
            }
        ],
        "output_text": record.output_text or "",
        "usage": record.usage,
    }


class ResponseManager:
    """Owns the Responses-API store + run wiring for one server."""

    def __init__(
        self,
        agent: "Agent",
        *,
        max_responses: int = 1024,
        store: "RecordStore | None" = None,
    ) -> None:
        self._agent = agent
        self._store: "RecordStore" = store or InMemoryRecordStore(
            max_per_namespace=max_responses
        )

    # -- store -------------------------------------------------------------

    def get(self, response_id: str) -> ResponseRecord | None:
        raw = self._store.get(_RESPONSE_NS, response_id)
        return _record_from_json(raw) if raw is not None else None

    def delete(self, response_id: str) -> bool:
        return self._store.delete(_RESPONSE_NS, response_id)

    def _persist(self, record: ResponseRecord) -> None:
        self._store.set(_RESPONSE_NS, record.id, _record_to_json(record))

    # -- build -------------------------------------------------------------

    def _conversation(self, request: "ResponsesRequest") -> list[ChatMessage]:
        """Assemble the run's messages: prior turns (chained) + this input."""
        base: list[ChatMessage] = []
        if request.previous_response_id:
            prior = self.get(request.previous_response_id)
            if prior is not None:
                base = list(prior.messages)
        messages = list(base)
        # Add the system instructions only when starting a fresh conversation.
        if request.instructions and not base:
            messages.append(ChatMessage(role="system", content=request.instructions))
        for role, text in request.input_messages():
            messages.append(ChatMessage(role=role, content=text))
        return messages

    def _run_input(
        self, request: "ResponsesRequest", messages: list[ChatMessage], run_id: str
    ) -> AgentRunInput:
        return AgentRunInput(
            messages=messages,
            run_id=run_id,
            agent_id=self._agent.defaults.agent_id,
            graph_preset=self._agent.defaults.graph_preset,
            temperature=request.temperature,
            max_tokens=request.max_output_tokens,
            app_metadata={"openai_model": request.model},
        )

    def prepare(
        self, request: "ResponsesRequest"
    ) -> tuple[list[ChatMessage], AgentRunInput]:
        """Build the chained conversation + run input for a request."""
        messages = self._conversation(request)
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        return messages, self._run_input(request, messages, run_id)

    def finalize(
        self,
        request: "ResponsesRequest",
        messages: list[ChatMessage],
        output: "AgentRunOutput",
        *,
        response_id: str | None = None,
    ) -> ResponseRecord:
        """Build the response record from a finished run; persist if requested."""
        record = self._record_from(request, messages, output, response_id=response_id)
        if request.store and record.status == "completed":
            self._persist(record)
        return record

    @staticmethod
    def new_response_id() -> str:
        return f"resp_{uuid.uuid4().hex[:12]}"

    async def create(
        self, request: "ResponsesRequest"
    ) -> tuple[ResponseRecord, "AgentRunOutput"]:
        """Run one response turn; persist it when ``store`` is set."""
        messages, run_input = self.prepare(request)
        output = await self._agent.run(run_input)
        return self.finalize(request, messages, output), output

    def _record_from(
        self,
        request: "ResponsesRequest",
        messages: list[ChatMessage],
        output: "AgentRunOutput",
        *,
        response_id: str | None = None,
    ) -> ResponseRecord:
        status = getattr(output.status, "value", output.status)
        answer = output.answer or ""
        usage_dict = responses_usage(output)
        record = ResponseRecord(
            id=response_id or self.new_response_id(),
            created=int(time.time()),
            model=request.model,
            status="completed" if status == "completed" else status,
            output_text=answer if status == "completed" else "",
            usage=usage_dict,
            messages=(
                list(messages) + [ChatMessage(role="assistant", content=answer)]
                if status == "completed"
                else list(messages)
            ),
        )
        return record


__all__ = ["ResponseManager", "ResponseRecord", "response_object"]
