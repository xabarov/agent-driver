"""Hook-chain self-healing wired through the run lifecycle seam."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_driver.contracts import AgentRunInput, ToolCall, ToolManifest
from agent_driver.contracts.enums import RuntimeEventType
from agent_driver.contracts.events import RuntimeEvent
from agent_driver.contracts.hook_chains import (
    HookAction,
    HookActionType,
    HookChainConfig,
    HookRule,
    HookTrigger,
    HookTriggerEvent,
)
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import (
    FallbackSpec,
    HookChainLifecycleHook,
    placeholders_for_event,
)
from agent_driver.runtime.single_agent.types import RunnerConfig
from agent_driver.sdk import ToolSet, create_agent
from agent_driver.tools import ToolRegistry


def _event(event_type: RuntimeEventType, payload: dict, seq: int = 1) -> RuntimeEvent:
    return RuntimeEvent(
        event_id=f"e{seq}",
        type=event_type,
        run_id="r1",
        attempt_id="a1",
        seq=seq,
        created_at="2026-06-09T00:00:00Z",
        payload=payload,
    )


def _run_failed_rule() -> HookChainConfig:
    return HookChainConfig(
        rules=[
            HookRule(
                name="recover_on_failure",
                trigger=HookTrigger(event=HookTriggerEvent.RUN_FAILED),
                action=HookAction(
                    type=HookActionType.SPAWN_FALLBACK,
                    agent_type="recovery",
                    prompt_template="Recover '{original_question}' (was: {error_message})",
                ),
            )
        ]
    )


def _ctx(question: str = "do the thing") -> SimpleNamespace:
    return SimpleNamespace(run_input=SimpleNamespace(input=question))


def test_placeholders_for_event_extracts_tool_and_error() -> None:
    event = _event(
        RuntimeEventType.TOOL_CALL_COMPLETED,
        {"tools": [{"tool_name": "chart", "error": "boom"}], "statuses": ["failed"]},
    )
    ph = placeholders_for_event(event, original_question="draw a chart")
    assert ph["tool_name"] == "chart"
    assert "boom" in ph["error_message"]
    assert ph["original_question"] == "draw a chart"


@pytest.mark.asyncio
async def test_adapter_spawns_fallback_on_run_failed() -> None:
    spawned: list[FallbackSpec] = []

    async def spawn(fallback: FallbackSpec, context) -> None:  # noqa: ANN001
        spawned.append(fallback)

    hook = HookChainLifecycleHook(_run_failed_rule(), spawn)
    events = [_event(RuntimeEventType.RUN_FAILED, {"reason": "model_error"})]
    await hook.on_error(_ctx("draw a chart"), output=SimpleNamespace(), events=events)

    assert len(spawned) == 1
    assert spawned[0].rule_name == "recover_on_failure"
    assert spawned[0].agent_type == "recovery"
    assert "draw a chart" in spawned[0].prompt


@pytest.mark.asyncio
async def test_adapter_no_rules_no_spawn() -> None:
    spawned = []
    hook = HookChainLifecycleHook(
        HookChainConfig(rules=[]), lambda fb, ctx: spawned.append(fb)  # type: ignore[arg-type]
    )
    await hook.on_error(
        _ctx(),
        output=SimpleNamespace(),
        events=[_event(RuntimeEventType.RUN_FAILED, {})],
    )
    assert spawned == []


@pytest.mark.asyncio
async def test_adapter_depth_limit_one_fires_once() -> None:
    spawned: list[FallbackSpec] = []

    async def spawn(fallback: FallbackSpec, context) -> None:  # noqa: ANN001
        spawned.append(fallback)

    # Two failing tool events, default depth_limit=1 -> one fallback only.
    cfg = HookChainConfig(
        rules=[
            HookRule(
                name="r",
                trigger=HookTrigger(event=HookTriggerEvent.TOOL_CALL_FAILED),
                action=HookAction(
                    type=HookActionType.SPAWN_FALLBACK,
                    agent_type="x",
                    prompt_template="retry {tool_name}",
                ),
            )
        ]
    )
    hook = HookChainLifecycleHook(cfg, spawn)
    events = [
        _event(RuntimeEventType.TOOL_CALL_COMPLETED, {"statuses": ["failed"]}, seq=1),
        _event(RuntimeEventType.TOOL_CALL_COMPLETED, {"statuses": ["failed"]}, seq=2),
    ]
    await hook.on_error(_ctx(), output=SimpleNamespace(), events=events)
    assert len(spawned) == 1


@pytest.mark.asyncio
async def test_adapter_isolates_spawn_errors() -> None:
    calls: list[str] = []

    async def flaky_spawn(fallback: FallbackSpec, context) -> None:  # noqa: ANN001
        calls.append(fallback.rule_name)
        raise RuntimeError("spawn failed")

    cfg = HookChainConfig(
        rules=[
            HookRule(
                name="a",
                trigger=HookTrigger(event=HookTriggerEvent.RUN_FAILED),
                action=HookAction(
                    type=HookActionType.SPAWN_FALLBACK,
                    agent_type="x",
                    prompt_template="p",
                ),
                depth_limit=5,
            )
        ]
    )
    hook = HookChainLifecycleHook(cfg, flaky_spawn)
    # Two RUN_FAILED events would fire twice; a spawn error must not abort.
    events = [
        _event(RuntimeEventType.RUN_FAILED, {}, seq=1),
        _event(RuntimeEventType.RUN_FAILED, {}, seq=2),
    ]
    await hook.on_error(_ctx(), output=SimpleNamespace(), events=events)
    assert calls == ["a", "a"]  # both attempted despite the first raising


class _AlwaysToolCallProvider(FakeProvider):
    """Provider that always requests a tool call (never a final answer)."""

    def __init__(self) -> None:
        super().__init__(response_text="unused")

    async def complete(self, request: LlmRequest) -> LlmResponse:
        return LlmResponse(
            message=ChatMessage(role="assistant", content=""),
            finish_reason=LlmFinishReason.TOOL_CALLS,
            provider="loop",
            model="test",
            metadata={
                "planned_tool_calls": [
                    ToolCall(tool_name="echo", tool_call_id="c1", args={}).model_dump(
                        mode="json"
                    )
                ]
            },
        )


def _echo_registry() -> ToolRegistry:
    registry = ToolRegistry()

    async def _echo(_args: dict) -> dict:
        return {"summary": "echo"}

    registry.register(
        ToolManifest(
            name="echo",
            description="No-op echo tool.",
            args_schema={"type": "object", "additionalProperties": True},
            output_type="json",
        ),
        _echo,
    )
    return registry


@pytest.mark.asyncio
async def test_runner_dispatches_on_error_for_failed_run() -> None:
    """A run that fails (max steps) triggers the hook-chain fallback spawn."""
    spawned: list[FallbackSpec] = []

    async def spawn(fallback: FallbackSpec, context) -> None:  # noqa: ANN001
        spawned.append(fallback)

    hook = HookChainLifecycleHook(_run_failed_rule(), spawn)
    agent = create_agent(
        provider=_AlwaysToolCallProvider(),
        tools=ToolSet.all(),
        config=RunnerConfig(tool_registry=_echo_registry()),
        lifecycle_hooks=(hook,),
    )
    output = await agent.run(
        AgentRunInput(
            input="keep going",
            run_id="run_fail",
            thread_id="t1",
            agent_id="agent",
            graph_preset="single_react",
            max_steps=2,
        )
    )
    assert output.status.value == "failed"
    assert len(spawned) == 1
    assert spawned[0].rule_name == "recover_on_failure"
    assert "keep going" in spawned[0].prompt
