"""Regression: final answer must not leak text-form tool_call markup."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_driver.contracts import AgentRunInput
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmFinishReason, LlmResponse, UsageSummary
from agent_driver.runtime.single_agent.output import SingleAgentOutputMixin
from agent_driver.runtime.single_agent.types import RunContext, TerminalResult


class _OutputHost(SingleAgentOutputMixin):
    """Minimal host for output mixin tests."""

    _deps = SimpleNamespace(
        session_store=SimpleNamespace(
            upsert_session=lambda *args, **kwargs: None,
            append_turn=lambda *args, **kwargs: None,
            save_digest=lambda *args, **kwargs: SimpleNamespace(
                digest_id="digest_test", turn_index=0
            ),
            list_turns=lambda *args, **kwargs: [],
            list_digests=lambda *args, **kwargs: [],
        ),
        artifact_store=SimpleNamespace(get=lambda *args, **kwargs: None),
        context_store=SimpleNamespace(attach_artifact=lambda *args, **kwargs: None),
        event_log=SimpleNamespace(list_for_run=lambda *args, **kwargs: []),
    )
    graph_id = "test_graph"


def test_sanitize_terminal_answer_strips_tool_call_block() -> None:
    """Text-form blocks should be removed from terminal answer."""
    host = _OutputHost()
    raw = (
        "Summary here.\n"
        '<tool_call>{"name":"read_file","arguments":{"path":"README.md"}}</tool_call>'
    )
    context = RunContext(
        run_input=AgentRunInput(
            input="test",
            run_id="run_strip_1",
            agent_id="agent",
            graph_preset="single_react",
        ),
        identifiers={"run_id": "run_strip_1", "attempt_id": "attempt_1"},
        llm_response=LlmResponse(
            message=ChatMessage(role="assistant", content=raw),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(),
            provider="fake",
            model="fake-model",
        ),
    )
    cleaned = host._sanitize_terminal_answer(context)
    assert cleaned == "Summary here."
    assert context.metadata.get("raw_assistant_content") == raw

