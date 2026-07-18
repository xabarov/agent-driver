"""History normalizer unit tests + protocol-tail fuzzing (epic 018 phase D)."""

from __future__ import annotations

import random

from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage
from agent_driver.runtime.single_agent.context_management.history_normalizer import (
    close_interrupted_tool_sequence,
    close_tool_tail_before_user_injection,
    fold_tool_history,
    repair_tool_call_arguments,
)
from agent_driver.runtime.single_agent.context_management.protocol_validate import (
    validate_and_repair_protocol_messages,
)


def _assistant_with_call(call_id: str = "call_1", args: str = "{}") -> ChatMessage:
    return ChatMessage(
        role=ChatRole.ASSISTANT,
        content="",
        metadata={
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": "search", "arguments": args},
                }
            ]
        },
    )


def _tool_result(call_id: str = "call_1", content: str = "payload") -> ChatMessage:
    return ChatMessage(
        role=ChatRole.TOOL, name="search", tool_call_id=call_id, content=content
    )


def test_fold_preserves_evidence_and_flags_provenance():
    messages = [
        ChatMessage(role=ChatRole.USER, content="go"),
        _assistant_with_call(),
        _tool_result(content="the evidence"),
    ]
    folded, changed = fold_tool_history(messages)
    assert changed is True
    assert all(m.role != ChatRole.TOOL for m in folded)
    assert any(
        m.role == ChatRole.USER
        and "the evidence" in m.content
        and (m.metadata or {}).get("folded_tool_result")
        for m in folded
    )
    # No tool protocol → no-op.
    plain = [ChatMessage(role=ChatRole.USER, content="hi")]
    same, changed2 = fold_tool_history(plain)
    assert changed2 is False and same == plain


def test_close_interrupted_only_touches_unanswered_tool_calls_tail():
    unanswered = [ChatMessage(role=ChatRole.USER, content="go"), _assistant_with_call()]
    closed, changed = close_interrupted_tool_sequence(list(unanswered))
    assert changed is True
    assert closed[-1].role == ChatRole.TOOL
    assert (closed[-1].metadata or {}).get("interrupted_tool_stub") is True
    # Trailing tool result is canonical pre-completion — untouched here…
    tool_tail = [_assistant_with_call(), _tool_result()]
    same, changed2 = close_interrupted_tool_sequence(list(tool_tail))
    assert changed2 is False
    # …but closed before a user injection (steering path).
    acked, changed3 = close_tool_tail_before_user_injection(list(tool_tail))
    assert changed3 is True and acked[-1].role == ChatRole.ASSISTANT


def test_repair_tool_call_arguments_keeps_history_sendable():
    broken = _assistant_with_call(args="{'single': 'quotes'")
    repaired, changed = repair_tool_call_arguments([broken])
    assert changed is True
    call = repaired[0].metadata["tool_calls"][0]
    assert call["function"]["arguments"] == "{}"
    assert "single" in call["function"]["raw_arguments"]
    assert repaired[0].metadata["tool_call_arguments_repaired"] is True
    # Valid JSON untouched.
    ok, changed2 = repair_tool_call_arguments([_assistant_with_call(args='{"q": 1}')])
    assert changed2 is False


def test_protocol_tail_fuzz_validator_never_breaks():
    """Deterministic fuzz: random protocol tails must always validate into a sendable,
    pair-consistent history without raising."""
    rng = random.Random(42)
    roles_pool = [
        "user",
        "assistant",
        "assistant_call",
        "tool",
        "tool_orphan",
        "broken_args",
    ]
    for case in range(300):
        length = rng.randint(1, 8)
        messages: list[ChatMessage] = [ChatMessage(role=ChatRole.USER, content="start")]
        open_call_ids: list[str] = []
        for i in range(length):
            kind = rng.choice(roles_pool)
            if kind == "user":
                messages.append(ChatMessage(role=ChatRole.USER, content=f"u{i}"))
            elif kind == "assistant":
                messages.append(ChatMessage(role=ChatRole.ASSISTANT, content=f"a{i}"))
            elif kind == "assistant_call":
                call_id = f"call_{case}_{i}"
                open_call_ids.append(call_id)
                messages.append(_assistant_with_call(call_id))
            elif kind == "tool" and open_call_ids:
                messages.append(_tool_result(open_call_ids.pop(0), content=f"t{i}"))
            elif kind == "tool_orphan":
                messages.append(_tool_result(f"orphan_{case}_{i}", content=f"o{i}"))
            else:
                messages.append(_assistant_with_call(f"b_{case}_{i}", args="{broken"))
        result = validate_and_repair_protocol_messages(messages)
        out = list(result.messages)
        assert out, f"case {case}: validator emptied a non-empty history"
        # Pair consistency: every kept tool result's id is answered by a PRECEDING call.
        seen_ids: set[str] = set()
        for message in out:
            if message.role == ChatRole.ASSISTANT:
                for call in (message.metadata or {}).get("tool_calls", []) or []:
                    if isinstance(call, dict) and call.get("id"):
                        seen_ids.add(str(call["id"]))
                        arguments = (call.get("function") or {}).get("arguments")
                        if isinstance(arguments, str) and arguments.strip():
                            import json as _json

                            _json.loads(arguments)  # repaired: must parse
            if message.role == ChatRole.TOOL and message.tool_call_id:
                assert message.tool_call_id in seen_ids, (
                    f"case {case}: orphan tool result survived validation"
                )
        # No unanswered tool_calls tail.
        tail = out[-1]
        if tail.role == ChatRole.ASSISTANT and (tail.metadata or {}).get("tool_calls"):
            raise AssertionError(f"case {case}: unanswered tool_calls tail survived")
