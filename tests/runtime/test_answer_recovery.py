"""Unit tests for finalize-time degenerate-answer recovery (epic 015, Phase C)."""

from __future__ import annotations

from types import SimpleNamespace

from agent_driver.runtime.single_agent.finalization.answer_recovery import (
    assistant_turn_contents,
    recover_degenerate_terminal_answer,
)

_LONG = "A complete corpus summary. " + "detail; " * 80  # > 400 chars
_LONGER = "A complete corpus summary. " + "detail; " * 120


def _evt(name: str, content: str = "", *, as_obj: bool = False):
    if as_obj:
        return SimpleNamespace(event=name, payload={"content": content})
    return {"event": name, "payload": {"content": content}}


def test_extract_contents_from_dict_and_object_events() -> None:
    events = [
        _evt("assistant_message_completed", "one"),
        _evt("tool_call_completed", ""),
        _evt("assistant_message_completed", "two", as_obj=True),
        {"type": "assistant_message_replaced", "payload": {"content": "three"}},
    ]
    assert assistant_turn_contents(events) == ["one", "two", "three"]


def test_extract_contents_from_runtime_event_objects_with_enum_type() -> None:
    """Real RuntimeEvent stores the name in ``.type`` (an enum with ``.value``), not ``.event``."""
    events = [
        SimpleNamespace(
            type=SimpleNamespace(value="assistant_message_completed"),
            payload={"content": "hi"},
        ),
        SimpleNamespace(type=SimpleNamespace(value="tool_call_completed"), payload={}),
        SimpleNamespace(
            type=SimpleNamespace(value="assistant_message_completed"),
            payload={"content": "bye"},
        ),
    ]
    assert assistant_turn_contents(events) == ["hi", "bye"]


def test_normal_single_answer_is_not_recovered() -> None:
    events = [_evt("assistant_message_completed", _LONG)]
    answer, reason = recover_degenerate_terminal_answer(
        events=events, terminal_answer=_LONG, tool_call_count=0
    )
    assert answer is None and reason is None


def test_degenerate_short_stub_recovers_longest_substantive_turn() -> None:
    events = [
        _evt("assistant_message_completed", _LONGER),
        _evt("assistant_message_completed", _LONG),
        _evt(
            "assistant_message_completed", "Задача уже выполнена, см. предыдущий ответ."
        ),
    ]
    answer, reason = recover_degenerate_terminal_answer(
        events=events,
        terminal_answer="Задача уже выполнена, см. предыдущий ответ.",
        tool_call_count=0,
    )
    assert answer == _LONGER
    assert reason == "degenerate_short_restatement"


def test_borderline_short_terminal_dwarfed_by_prior_is_recovered() -> None:
    """A ~200-char terminal (no stub markers) dwarfed by a ~5k prior turn is still degenerate."""
    borderline = "x" * 210  # not empty, no stub markers, but tiny vs _LONGER
    events = [
        _evt("assistant_message_completed", _LONGER),
        _evt("assistant_message_completed", borderline),
    ]
    answer, reason = recover_degenerate_terminal_answer(
        events=events, terminal_answer=borderline, tool_call_count=0
    )
    assert answer == _LONGER
    assert reason == "degenerate_short_restatement"


def test_empty_terminal_recovers_prior_substantive() -> None:
    events = [
        _evt("assistant_message_completed", _LONG),
        _evt("assistant_message_completed", ""),
    ]
    answer, reason = recover_degenerate_terminal_answer(
        events=events, terminal_answer="", tool_call_count=0
    )
    assert answer == _LONG
    assert reason == "empty_terminal_answer"


def test_tool_informed_run_is_never_overridden() -> None:
    events = [
        _evt("assistant_message_completed", _LONG),
        _evt("assistant_message_completed", "short post-tool answer"),
    ]
    answer, reason = recover_degenerate_terminal_answer(
        events=events, terminal_answer="short post-tool answer", tool_call_count=2
    )
    assert answer is None and reason is None


def test_genuine_short_answer_without_long_prior_is_kept() -> None:
    events = [
        _evt(
            "assistant_message_completed", "The most popular topic is neural networks."
        )
    ]
    answer, reason = recover_degenerate_terminal_answer(
        events=events,
        terminal_answer="The most popular topic is neural networks.",
        tool_call_count=0,
    )
    assert answer is None and reason is None
