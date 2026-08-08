"""Planning P2 — nudge to CREATE a todo checklist for multi-step tasks."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_driver.contracts import AgentRunInput
from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime.single_agent.llm_step.prompt import _has_existing_todos
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet

_CHECKLIST = "no checklist exists yet"
_PLAN_MODE = "prefer enter_plan_mode"


# --- _has_existing_todos unit --------------------------------------------------


def test_has_existing_todos() -> None:
    with_todos = SimpleNamespace(
        metadata={"planning_state": {"todos": [{"todo_id": "t1", "content": "x"}]}}
    )
    without = SimpleNamespace(metadata={"planning_state": {"todos": []}})
    missing = SimpleNamespace(metadata={})
    assert _has_existing_todos(with_todos) is True
    assert _has_existing_todos(without) is False
    assert _has_existing_todos(missing) is False


# --- integration: the nudge picks the right planning tool ----------------------


class _Recorder(FakeProvider):
    def __init__(self) -> None:
        super().__init__(response_text="готово")
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return await super().complete(request)


async def _run(tools: ToolSet, run_id: str, *, hint: bool = True) -> str:
    provider = _Recorder()
    metadata = {}
    if hint:
        metadata["planning_hint"] = {"level": "suggested", "reason": "multi-step"}
    await create_agent(provider=provider, tools=tools).run(
        AgentRunInput(
            input="сделай многошаговую задачу",
            run_id=run_id,
            agent_id="agent",
            graph_preset="single_react",
            app_metadata={"chat_mode": True},
            tool_policy={"metadata": metadata},
        )
    )
    return "\n".join(m.content or "" for r in provider.requests for m in r.messages)


@pytest.mark.asyncio
async def test_checklist_nudge_when_only_todo_write_available() -> None:
    text = await _run(ToolSet.only("todo_write"), "p2_todo_only")
    assert _CHECKLIST in text
    assert _PLAN_MODE not in text


@pytest.mark.asyncio
async def test_plan_mode_preferred_when_available() -> None:
    text = await _run(ToolSet.only("todo_write", "enter_plan_mode"), "p2_both")
    assert _PLAN_MODE in text
    assert _CHECKLIST not in text  # deferred to the approval-plan path


@pytest.mark.asyncio
async def test_no_nudge_when_no_planning_hint() -> None:
    text = await _run(ToolSet.only("todo_write"), "p2_nohint", hint=False)
    assert _CHECKLIST not in text
    assert _PLAN_MODE not in text


@pytest.mark.asyncio
async def test_no_checklist_nudge_without_todo_tool() -> None:
    text = await _run(ToolSet.only(), "p2_notool")
    assert _CHECKLIST not in text
    assert _PLAN_MODE not in text
