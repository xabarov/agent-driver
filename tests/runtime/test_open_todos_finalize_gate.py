"""Planning P1 — a run must not finalize while the session plan has open todos."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_driver.contracts import AgentRunInput, ToolCall
from agent_driver.contracts.context import PlanningState, TodoState
from agent_driver.contracts.enums import PlanningTodoStatus, RunStatus
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime.single_agent.context_management.todo_reminders import (
    format_open_todos_finalize_reminder,
    maybe_append_todo_reminder_to_protocol,
)
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet


def _state(*todos: tuple[str, str, PlanningTodoStatus]) -> PlanningState:
    return PlanningState(
        run_id="r",
        todos=[
            TodoState(todo_id=tid, content=content, status=status)
            for tid, content, status in todos
        ],
    )


# --- formatter unit ------------------------------------------------------------


def test_format_lists_only_unfinished() -> None:
    state = _state(
        ("t1", "do first", PlanningTodoStatus.COMPLETED),
        ("t2", "do second", PlanningTodoStatus.IN_PROGRESS),
        ("t3", "do third", PlanningTodoStatus.PENDING),
    )
    text = format_open_todos_finalize_reminder(state)
    assert "attempted to give a final answer" in text
    assert "do second" in text and "do third" in text
    assert "do first" not in text  # completed items are not re-listed
    assert "completed or cancelled" in text


# --- injection unit ------------------------------------------------------------


def test_blocked_marker_injects_reminder_and_clears() -> None:
    state = _state(("t1", "do the thing", PlanningTodoStatus.PENDING))
    ctx = SimpleNamespace(
        metadata={
            "planning_state": state.model_dump(mode="json"),
            "open_todos_finalize_blocked": True,
        }
    )
    out = maybe_append_todo_reminder_to_protocol(ctx, tuple())
    assert out is not None and len(out) == 1
    assert "attempted to give a final answer" in (out[0].content or "")
    # one-shot: the marker is consumed so it rides exactly the re-prompt turn
    assert "open_todos_finalize_blocked" not in ctx.metadata


def test_no_marker_no_reminder_below_threshold() -> None:
    state = _state(("t1", "x", PlanningTodoStatus.PENDING))
    ctx = SimpleNamespace(metadata={"planning_state": state.model_dump(mode="json")})
    # no blocked marker + loops below threshold → unchanged
    assert maybe_append_todo_reminder_to_protocol(ctx, tuple()) == tuple()


# --- integration ---------------------------------------------------------------


class _RecordingFinalizer(FakeProvider):
    """Records requests and always tries to finish with a plain final answer."""

    def __init__(self) -> None:
        super().__init__(response_text="Готово, вот итоговый ответ.")
        self.requests: list = []

    async def complete(self, request):  # type: ignore[override]
        self.requests.append(request)
        return await super().complete(request)


@pytest.mark.asyncio
async def test_run_reprompts_when_finalizing_with_open_todos() -> None:
    """A planned todo_write leaves one PENDING todo; the model then tries to finish.
    The gate re-prompts (bounded) with the open-todos reminder, then lets it complete."""
    provider = _RecordingFinalizer()
    out = await create_agent(
        provider=provider,
        tools=ToolSet.only("todo_write"),
    ).run(
        AgentRunInput(
            input="сделай многошаговую задачу",
            run_id="p1_gate",
            agent_id="agent",
            graph_preset="single_react",
            app_metadata={"chat_mode": True},
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="todo_write",
                            tool_call_id="c1",
                            args={
                                "todos": [
                                    {
                                        "id": "t1",
                                        "content": "выполнить основной шаг",
                                        "status": "pending",
                                    }
                                ]
                            },
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )

    # The gate fired: a warning signal was emitted at least once.
    signals = [e.payload.get("signal_id") for e in out.events]
    assert "open_todos_finalize_blocked" in signals
    # The strong reminder reached the model on a re-prompt turn.
    joined = "\n".join(
        m.content or "" for req in provider.requests for m in req.messages
    )
    assert "attempted to give a final answer" in joined
    # Bounded: the run still terminates (does not spin forever).
    assert out.status in {RunStatus.COMPLETED, RunStatus.FAILED}


@pytest.mark.asyncio
async def test_no_gate_without_todos() -> None:
    """A run with no planning state finalizes immediately — the gate is inert."""
    provider = _RecordingFinalizer()
    out = await create_agent(provider=provider, tools=ToolSet.only()).run(
        AgentRunInput(
            input="просто ответь",
            run_id="p1_notodos",
            agent_id="agent",
            graph_preset="single_react",
            app_metadata={"chat_mode": True},
        )
    )
    signals = [e.payload.get("signal_id") for e in out.events]
    assert "open_todos_finalize_blocked" not in signals
    assert out.status == RunStatus.COMPLETED
