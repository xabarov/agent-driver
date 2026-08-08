"""Planning P5 — nudge to verify a completed multi-step plan before finalizing."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_driver.contracts import AgentRunInput, ToolCall
from agent_driver.contracts.context import PlanningState, TodoState
from agent_driver.contracts.enums import PlanningTodoStatus as S
from agent_driver.contracts.enums import RunStatus
from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime.single_agent.context_management.todo_reminders import (
    format_verify_before_final_reminder,
    maybe_append_todo_reminder_to_protocol,
    plan_all_done_without_verification,
)
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet


def _state(*todos: tuple[str, str, S]) -> PlanningState:
    return PlanningState(
        run_id="r",
        todos=[TodoState(todo_id=i, content=c, status=s) for i, c, s in todos],
    )


# --- detector unit -------------------------------------------------------------


def test_detects_completed_plan_without_verification() -> None:
    state = _state(
        ("a", "load data", S.COMPLETED),
        ("b", "aggregate", S.COMPLETED),
        ("c", "write summary", S.CANCELLED),
    )
    assert plan_all_done_without_verification(state) is True


def test_verification_step_suppresses() -> None:
    state = _state(
        ("a", "load data", S.COMPLETED),
        ("b", "aggregate", S.COMPLETED),
        ("c", "verify the totals", S.COMPLETED),
    )
    assert plan_all_done_without_verification(state) is False


def test_ru_verification_step_suppresses() -> None:
    state = _state(
        ("a", "загрузить", S.COMPLETED),
        ("b", "посчитать", S.COMPLETED),
        ("c", "перепроверить итоги", S.COMPLETED),
    )
    assert plan_all_done_without_verification(state) is False


def test_below_min_steps_or_unfinished_returns_false() -> None:
    assert (
        plan_all_done_without_verification(
            _state(("a", "x", S.COMPLETED), ("b", "y", S.COMPLETED))
        )
        is False
    )
    assert (
        plan_all_done_without_verification(
            _state(
                ("a", "x", S.COMPLETED),
                ("b", "y", S.COMPLETED),
                ("c", "z", S.IN_PROGRESS),
            )
        )
        is False
    )


# --- injection unit ------------------------------------------------------------


def test_marker_injects_verify_reminder_and_clears() -> None:
    state = _state(
        ("a", "x", S.COMPLETED), ("b", "y", S.COMPLETED), ("c", "z", S.COMPLETED)
    )
    ctx = SimpleNamespace(
        metadata={
            "planning_state": state.model_dump(mode="json"),
            "verify_before_final_blocked": True,
        }
    )
    out = maybe_append_todo_reminder_to_protocol(ctx, tuple())
    assert out is not None and len(out) == 1
    assert "verify your work" in (out[0].content or "")
    assert "verify_before_final_blocked" not in ctx.metadata


def test_verify_reminder_mentions_step_count() -> None:
    text = format_verify_before_final_reminder(
        _state(("a", "x", S.COMPLETED), ("b", "y", S.COMPLETED), ("c", "z", S.COMPLETED))
    )
    assert "3-step plan" in text
    assert "verify" in text


# --- integration ---------------------------------------------------------------


class _Recorder(FakeProvider):
    def __init__(self) -> None:
        super().__init__(response_text="Готово, вот итоговый ответ.")
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return await super().complete(request)


def _done(*contents: str) -> list[dict]:
    return [
        {"id": f"t{i}", "content": c, "status": "completed"}
        for i, c in enumerate(contents)
    ]


async def _run(run_id: str, todos: list[dict], *, contract: bool = False):
    provider = _Recorder()
    metadata: dict = {
        "planned_tool_calls": [
            ToolCall(
                tool_name="todo_write", tool_call_id="c1", args={"todos": todos}
            ).model_dump(mode="json")
        ]
    }
    if contract:
        metadata["deliverable_request"] = {"enabled": True}
    out = await create_agent(provider=provider, tools=ToolSet.only("todo_write")).run(
        AgentRunInput(
            input="сделай план",
            run_id=run_id,
            agent_id="agent",
            graph_preset="single_react",
            app_metadata={"chat_mode": True},
            tool_policy={"metadata": metadata},
        )
    )
    signals = [e.payload.get("signal_id") for e in out.events]
    text = "\n".join(m.content or "" for r in provider.requests for m in r.messages)
    return signals, text, out.status


@pytest.mark.asyncio
async def test_gate_reprompts_completed_plan_without_verification() -> None:
    signals, text, status = await _run(
        "p5_nudge", _done("load data", "aggregate", "write summary")
    )
    assert "plan_verification_nudge" in signals
    assert "verify your work" in text
    assert status == RunStatus.COMPLETED  # bounded — still finishes


@pytest.mark.asyncio
async def test_gate_skips_when_a_verification_step_exists() -> None:
    signals, _text, status = await _run(
        "p5_hasverif", _done("load data", "aggregate", "verify the totals")
    )
    assert "plan_verification_nudge" not in signals
    assert status == RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_gate_defers_to_contract_runs() -> None:
    signals, _text, _status = await _run(
        "p5_contract", _done("load data", "aggregate", "write summary"), contract=True
    )
    assert "plan_verification_nudge" not in signals
