"""Epic 042 A/C: tool_call_id dedup + no repair-execute on non-terminal truncation."""

from __future__ import annotations

from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.tools import ToolCall
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.contracts import LlmFinishReason, LlmResponse
from agent_driver.tools.executor.planned import (
    dedupe_tool_call_ids,
    extract_planned_tool_calls,
)


def _response(planned: list[dict], *, finish: LlmFinishReason, content: str = "") -> LlmResponse:
    return LlmResponse(
        message=ChatMessage(role="assistant", content=content),
        finish_reason=finish,
        usage=UsageSummary(model_provider="fake", model_name="m"),
        provider="fake",
        model="m",
        metadata={"planned_tool_calls": planned, "suppress_text_form_tool_calls": True},
    )


# ---- A: tool_call_id collision dedup ----

def test_dedupe_renames_colliding_ids_deterministically() -> None:
    calls = [
        ToolCall(tool_name="a", tool_call_id="c1", args={}),
        ToolCall(tool_name="b", tool_call_id="c1", args={}),
        ToolCall(tool_name="c", tool_call_id="c1", args={}),
    ]
    out = dedupe_tool_call_ids(calls)
    assert [c.tool_call_id for c in out] == ["c1", "c1_d1", "c1_d2"]
    # deterministic — a second pass over the (now unique) ids is a no-op.
    assert [c.tool_call_id for c in dedupe_tool_call_ids(out)] == ["c1", "c1_d1", "c1_d2"]


def test_dedupe_leaves_none_ids_alone() -> None:
    calls = [
        ToolCall(tool_name="a", tool_call_id=None, args={}),
        ToolCall(tool_name="b", tool_call_id=None, args={}),
    ]
    out = dedupe_tool_call_ids(calls)
    assert [c.tool_call_id for c in out] == [None, None]


def test_extract_dedupes_colliding_provider_ids() -> None:
    resp = _response(
        [
            ToolCall(tool_name="a", tool_call_id="dup", args={}).model_dump(mode="json"),
            ToolCall(tool_name="b", tool_call_id="dup", args={}).model_dump(mode="json"),
        ],
        finish=LlmFinishReason.TOOL_CALLS,
    )
    ids = [c.tool_call_id for c in extract_planned_tool_calls(resp)]
    assert ids == ["dup", "dup_d1"]  # second result no longer collides


# ---- C: no repair-execute on non-terminal truncation ----

def _repaired_call() -> dict:
    return ToolCall(
        tool_name="bash",
        tool_call_id="c1",
        args={"command": "ls"},
        metadata={"text_form_args_repaired": True},
    ).model_dump(mode="json")


def test_truncated_repaired_call_dropped_when_no_terminal_reason() -> None:
    resp = _response([_repaired_call()], finish=LlmFinishReason.UNKNOWN)
    assert extract_planned_tool_calls(resp) == []  # never execute a half command


def test_repaired_call_kept_when_provider_gave_terminal_reason() -> None:
    for finish in (LlmFinishReason.TOOL_CALLS, LlmFinishReason.STOP, LlmFinishReason.LENGTH):
        resp = _response([_repaired_call()], finish=finish)
        calls = extract_planned_tool_calls(resp)
        assert len(calls) == 1, finish
        assert calls[0].tool_name == "bash"


def test_non_repaired_call_kept_even_without_terminal_reason() -> None:
    clean = ToolCall(tool_name="bash", tool_call_id="c1", args={"command": "ls"}).model_dump(
        mode="json"
    )
    resp = _response([clean], finish=LlmFinishReason.UNKNOWN)
    assert len(extract_planned_tool_calls(resp)) == 1  # only repaired calls are gated
