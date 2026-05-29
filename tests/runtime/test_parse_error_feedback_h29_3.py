"""Phase 13 H29.3 wire-up — tests for parse-error feedback injection.

Provider's normalization populates
``LlmResponse.metadata["tool_call_parse_errors"]`` whenever
``extract_text_form_tool_calls`` drops a malformed
``<tool_call>{...}</tool_call>`` block. Previously those errors
propagated to stream metadata but the LLM never saw feedback. Now
``_append_tool_call_parse_error_feedback`` formats them via the
H29.3 helpers and appends a user-role ChatMessage so the next turn
can self-correct.

Pins:
  * Helper inserts ONE user message when parse_errors present AND
    the protocol already includes tool messages (i.e. some calls did
    succeed).
  * Helper is a no-op when:
      - no parse_errors;
      - no llm_response on the context;
      - no tool-role messages have been added yet (pure-malformed
        turns shouldn't get a dangling user note).
  * Each parse-error code maps to a specific feedback line via the
    fallback_feedback helpers (missing_tool_name, arguments_json_*,
    payload_json_*, tool_call_validation_failed).
  * Dedupe: repeating the same parse_errors on the next turn does
    NOT re-insert; only NEW (unseen) errors trigger the message.
  * Output is bounded — at most 5 errors per message so a noisy
    model can't blow context.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_driver.contracts.messages import ChatMessage, ChatRole
from agent_driver.contracts.tools import ToolCall, ToolResultEnvelope
from agent_driver.contracts.enums import ToolPolicyDecision
from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.runtime.single_agent.tool_stage import (
    _append_tool_call_parse_error_feedback,
)
from agent_driver.runtime.tools import ToolExecutionResult


def _ctx(parse_errors: list[dict] | None) -> SimpleNamespace:
    response = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=""),
        provider="fake",
        model="fake",
        metadata={"tool_call_parse_errors": parse_errors or []},
    )
    return SimpleNamespace(metadata={}, llm_response=response)


def _result_with_one_envelope() -> ToolExecutionResult:
    return ToolExecutionResult(
        envelopes=[
            ToolResultEnvelope(
                call=ToolCall(tool_name="search", args={}),
                decision=ToolPolicyDecision.ALLOW,
            )
        ]
    )


def _messages_with_tool() -> list[ChatMessage]:
    return [
        ChatMessage(role=ChatRole.USER, content="start"),
        ChatMessage(role=ChatRole.ASSISTANT, content=""),
        ChatMessage(role=ChatRole.TOOL, content="ok", tool_call_id="call_1"),
    ]


# --- no-op gates ----------------------------------------------------------


def test_no_errors_no_message():
    ctx = _ctx([])
    messages = _messages_with_tool()
    before = len(messages)
    _append_tool_call_parse_error_feedback(ctx, _result_with_one_envelope(), messages)
    assert len(messages) == before


def test_no_llm_response_no_message():
    ctx = SimpleNamespace(metadata={}, llm_response=None)
    messages = _messages_with_tool()
    before = len(messages)
    _append_tool_call_parse_error_feedback(ctx, _result_with_one_envelope(), messages)
    assert len(messages) == before


def test_no_tool_messages_yet_no_feedback():
    """Pure-malformed turn (no tool messages emitted) → no dangling user note."""
    ctx = _ctx([{"error": "missing_tool_name"}])
    messages = [ChatMessage(role=ChatRole.USER, content="hello")]
    before = len(messages)
    _append_tool_call_parse_error_feedback(ctx, _result_with_one_envelope(), messages)
    assert len(messages) == before


# --- format dispatch -----------------------------------------------------


def test_missing_tool_name_feedback():
    ctx = _ctx([{"error": "missing_tool_name", "source": "tool_call_block", "index": 0}])
    messages = _messages_with_tool()
    _append_tool_call_parse_error_feedback(ctx, _result_with_one_envelope(), messages)
    assert len(messages) == 4
    body = messages[-1].content
    assert messages[-1].role == ChatRole.USER
    assert "Note: the runtime detected malformed tool-call blocks" in body
    assert '"name"' in body  # the helper's actionable hint


def test_arguments_json_parse_failed_feedback():
    ctx = _ctx(
        [
            {
                "error": "arguments_json_parse_failed",
                "source": "tool_call_block",
                "index": 0,
                "tool_name": "search",
                "raw_arguments": "{bad: 'json'}",
            }
        ]
    )
    messages = _messages_with_tool()
    _append_tool_call_parse_error_feedback(ctx, _result_with_one_envelope(), messages)
    assert len(messages) == 4
    body = messages[-1].content
    assert "'search'" in body
    assert "could not be parsed as JSON" in body
    assert "{bad: 'json'}" in body


def test_payload_json_parse_failed_feedback_includes_snippet():
    ctx = _ctx(
        [
            {
                "error": "payload_json_parse_failed",
                "source": "tool_call_block",
                "index": 0,
                "raw_payload": '{not valid json',
            }
        ]
    )
    messages = _messages_with_tool()
    _append_tool_call_parse_error_feedback(ctx, _result_with_one_envelope(), messages)
    body = messages[-1].content
    assert "Tool-call block JSON failed to parse" in body
    assert "{not valid json" in body  # snippet present


def test_unknown_error_code_falls_through_diagnostic():
    ctx = _ctx([{"error": "some_new_code", "source": "tool_call_block", "index": 0}])
    messages = _messages_with_tool()
    _append_tool_call_parse_error_feedback(ctx, _result_with_one_envelope(), messages)
    body = messages[-1].content
    # Even when we don't have a specific helper, the error code surfaces.
    assert "some_new_code" in body


# --- dedupe ---------------------------------------------------------------


def test_dedupe_repeat_does_not_reinsert():
    """Same parse errors next turn — no duplicate feedback message."""
    ctx = _ctx(
        [
            {
                "error": "missing_tool_name",
                "source": "tool_call_block",
                "index": 0,
            }
        ]
    )
    # Turn 1 — helper inserts.
    messages_t1 = _messages_with_tool()
    _append_tool_call_parse_error_feedback(ctx, _result_with_one_envelope(), messages_t1)
    assert len(messages_t1) == 4
    sent_keys_after_t1 = list(ctx.metadata.get("parse_error_feedback_sent_keys") or [])
    assert sent_keys_after_t1, "first call should record sent keys"

    # Turn 2 — same context (carries sent_keys), same parse_errors → no insert.
    messages_t2 = _messages_with_tool()
    _append_tool_call_parse_error_feedback(ctx, _result_with_one_envelope(), messages_t2)
    assert len(messages_t2) == 3  # no new user message


def test_dedupe_lets_new_error_through():
    """If a NEW (unseen) parse error appears, helper inserts feedback even
    if previous errors were already deduped."""
    # Turn 1 — record one error key.
    ctx = _ctx([{"error": "missing_tool_name", "source": "tool_call_block", "index": 0}])
    messages_t1 = _messages_with_tool()
    _append_tool_call_parse_error_feedback(ctx, _result_with_one_envelope(), messages_t1)
    assert len(messages_t1) == 4

    # Turn 2 — DIFFERENT error.
    ctx.llm_response = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=""),
        provider="fake",
        model="fake",
        metadata={
            "tool_call_parse_errors": [
                {
                    "error": "arguments_json_parse_failed",
                    "source": "tool_call_block",
                    "index": 1,
                    "tool_name": "search",
                    "raw_arguments": "{}",
                }
            ]
        },
    )
    messages_t2 = _messages_with_tool()
    _append_tool_call_parse_error_feedback(ctx, _result_with_one_envelope(), messages_t2)
    # New error → new feedback.
    assert len(messages_t2) == 4


# --- bounded output -------------------------------------------------------


def test_cap_at_five_errors_per_message():
    """Noisy model emitting 20 broken blocks shouldn't blow context."""
    ctx = _ctx(
        [
            {"error": "missing_tool_name", "source": "tool_call_block", "index": i}
            for i in range(20)
        ]
    )
    messages = _messages_with_tool()
    _append_tool_call_parse_error_feedback(ctx, _result_with_one_envelope(), messages)
    body = messages[-1].content
    # 5 lines max, each starts with "- ".
    line_count = body.count("\n- ")
    assert line_count <= 5
