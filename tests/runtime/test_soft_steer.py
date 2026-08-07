"""Steering: soft steer (STEER_USER_MESSAGE) — fold guidance into the current turn."""

from __future__ import annotations

from agent_driver.contracts import AgentRunInput
from agent_driver.contracts.control import (
    ControlKind,
    ControlPriority,
    ControlRequest,
    LiveMessageSemantic,
    requested_semantic_for_request,
)
from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage
from agent_driver.runtime.control.dispatcher import _apply_soft_steer
from agent_driver.runtime.single_agent.types import RunContext


def _ctx(messages: list[ChatMessage]) -> RunContext:
    return RunContext(
        run_input=AgentRunInput(
            input="do it",
            run_id="r1",
            agent_id="a",
            graph_preset="single_react",
            messages=messages,
        ),
        identifiers={},
    )


def test_steer_user_message_maps_to_steer_current() -> None:
    req = ControlRequest(
        kind=ControlKind.STEER_USER_MESSAGE,
        priority=ControlPriority.NOW,
        run_id="r1",
        payload={"message": "x"},
    )
    assert requested_semantic_for_request(req) is LiveMessageSemantic.STEER_CURRENT


def test_soft_steer_folds_into_last_tool_message() -> None:
    ctx = _ctx(
        [
            ChatMessage(role=ChatRole.USER, content="task"),
            ChatMessage(role=ChatRole.ASSISTANT, content="calling tool"),
            ChatMessage(role=ChatRole.TOOL, content="tool result"),
        ]
    )
    _apply_soft_steer(ctx, "focus on column B", queue_id="q1")
    out = ctx.run_input.messages
    assert len(out) == 3  # no new user turn appended
    assert out[-1].role == ChatRole.TOOL  # folded into the tool result
    assert "[User steering: focus on column B]" in str(out[-1].content)
    assert out[-1].metadata.get("live_message_queue_id") == "q1"


def test_soft_steer_degrades_to_user_turn_without_tool_message() -> None:
    ctx = _ctx([ChatMessage(role=ChatRole.USER, content="task")])
    _apply_soft_steer(ctx, "guidance", queue_id="q2")
    out = ctx.run_input.messages
    assert len(out) == 2  # no tool message -> a normal user turn, never dropped
    assert out[-1].role == ChatRole.USER
    assert out[-1].content == "guidance"


def test_soft_steer_is_idempotent_by_queue_id() -> None:
    ctx = _ctx(
        [
            ChatMessage(
                role=ChatRole.TOOL,
                content="r",
                metadata={"live_message_queue_id": "q3"},
            )
        ]
    )
    _apply_soft_steer(ctx, "again", queue_id="q3")
    assert "[User steering" not in str(ctx.run_input.messages[0].content)
