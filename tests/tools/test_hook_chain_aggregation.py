"""Phase 12 H22 — tests for hook chain aggregation.

Pins:
* HookResponse(value=...) replaces chain value (same as bare return);
* HookResponse(prevent_continuation=True) skips subsequent hooks;
* HookResponse(additional_context=...) accumulates across hooks;
  later hook sees earlier hooks' context contributions;
* Hook ``timeout_seconds`` bounds wall-clock; timeout falls back to
  pre-hook value (same as exception);
* Chain context surfaces into final envelope.metadata['hook_chain_context'];
* Legacy bare-value returns (H15 shape) still work — backwards compat.
"""

from __future__ import annotations

import asyncio

import pytest

from agent_driver.contracts import (
    AgentRunInput,
    ApprovalMode,
    SideEffectClass,
    ToolCall,
    ToolManifest,
    ToolPolicyInput,
    ToolPolicyMode,
    ToolRisk,
)
from agent_driver.contracts.hooks import BaseToolHook, HookResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.tools import GovernedToolExecutor, ToolRegistry
from tests.runtime.conftest import llm_request_with_planned_calls


def _build_run_input(run_id: str) -> AgentRunInput:
    return AgentRunInput(
        input="hello",
        run_id=run_id,
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
    )


def _register_echo(registry: ToolRegistry) -> list[dict]:
    seen: list[dict] = []

    async def _echo(args):
        seen.append(dict(args))
        return {"summary": f"echoed:{args.get('value', '')}"}

    registry.register(
        ToolManifest(
            name="echo",
            description="echo",
            risk=ToolRisk.LOW,
            side_effect=SideEffectClass.READ_ONLY,
            approval_mode=ApprovalMode.NEVER,
            idempotent=True,
            output_char_budget=2000,
        ),
        _echo,
    )
    return seen


# -- HookResponse value semantics ------------------------------------------


@pytest.mark.asyncio
async def test_hook_response_value_replaces_chain_value():
    """HookResponse(value=ToolCall(...)) replaces the chain value
    same as bare ToolCall return."""
    registry = ToolRegistry()
    seen = _register_echo(registry)

    class Wrap(BaseToolHook):
        name = "wrap"

        async def pre_tool_use(self, call, _ctx):
            return HookResponse(value=call.model_copy(update={"args": {"value": "WRAPPED"}}))

    executor = GovernedToolExecutor(registry=registry, tool_hooks=[Wrap()])
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="echo", args={"value": "raw"})]
        )
    )
    await executor.execute(_build_run_input("r_wrap"), response)
    assert seen[0]["value"] == "WRAPPED"


@pytest.mark.asyncio
async def test_hook_response_value_none_keeps_chain_value():
    """HookResponse(value=None) is equivalent to bare None — no replace."""
    registry = ToolRegistry()
    seen = _register_echo(registry)

    class ContextOnly(BaseToolHook):
        name = "context_only"

        async def pre_tool_use(self, _call, _ctx):
            # No value replacement, but contributes additional_context.
            return HookResponse(additional_context={"audit_trail": "step1"})

    executor = GovernedToolExecutor(registry=registry, tool_hooks=[ContextOnly()])
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="echo", args={"value": "unchanged"})]
        )
    )
    await executor.execute(_build_run_input("r_ctx_only"), response)
    assert seen[0]["value"] == "unchanged"


# -- prevent_continuation early exit ---------------------------------------


@pytest.mark.asyncio
async def test_prevent_continuation_stops_chain_after_hook():
    """When hook A returns prevent_continuation=True, hook B doesn't run."""
    registry = ToolRegistry()
    seen = _register_echo(registry)
    invocations: list[str] = []

    class FirstHook(BaseToolHook):
        name = "first"

        async def pre_tool_use(self, call, _ctx):
            invocations.append("first")
            return HookResponse(
                value=call.model_copy(update={"args": {"value": "FIRST"}}),
                prevent_continuation=True,
            )

    class SecondHook(BaseToolHook):
        name = "second"

        async def pre_tool_use(self, call, _ctx):
            invocations.append("second")
            return call.model_copy(update={"args": {"value": "SECOND"}})

    executor = GovernedToolExecutor(
        registry=registry, tool_hooks=[FirstHook(), SecondHook()]
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="echo", args={"value": "raw"})]
        )
    )
    await executor.execute(_build_run_input("r_prevent"), response)
    assert invocations == ["first"]
    assert seen[0]["value"] == "FIRST"


# -- additional_context accumulation ---------------------------------------


@pytest.mark.asyncio
async def test_additional_context_accumulates_across_hooks():
    """Hook B sees hook A's contribution in its context arg."""
    registry = ToolRegistry()
    _register_echo(registry)
    context_seen_by_b: dict[str, object] = {}

    class HookA(BaseToolHook):
        name = "a"

        async def pre_tool_use(self, _call, _ctx):
            return HookResponse(additional_context={"from_a": "alpha"})

    class HookB(BaseToolHook):
        name = "b"

        async def pre_tool_use(self, _call, ctx):
            context_seen_by_b.update(ctx)
            return None

    executor = GovernedToolExecutor(
        registry=registry, tool_hooks=[HookA(), HookB()]
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="echo", args={"value": "x"})]
        )
    )
    await executor.execute(_build_run_input("r_ctx"), response)
    assert context_seen_by_b.get("from_a") == "alpha"


@pytest.mark.asyncio
async def test_post_hook_chain_context_surfaces_in_envelope_metadata():
    """Post-hook additional_context lands in envelope.metadata
    under 'hook_chain_context'."""
    registry = ToolRegistry()
    _register_echo(registry)

    class TaggerA(BaseToolHook):
        name = "tagger_a"

        async def post_tool_use(self, _envelope, _ctx):
            return HookResponse(additional_context={"latency_ms": 42})

    class TaggerB(BaseToolHook):
        name = "tagger_b"

        async def post_tool_use(self, _envelope, _ctx):
            return HookResponse(additional_context={"trace_id": "abc-123"})

    executor = GovernedToolExecutor(
        registry=registry, tool_hooks=[TaggerA(), TaggerB()]
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="echo", args={"value": "v"})]
        )
    )
    result = await executor.execute(_build_run_input("r_meta_ctx"), response)
    chain_ctx = result.envelopes[0].metadata.get("hook_chain_context") or {}
    assert chain_ctx.get("latency_ms") == 42
    assert chain_ctx.get("trace_id") == "abc-123"


# -- timeout isolation ------------------------------------------------------


@pytest.mark.asyncio
async def test_hook_timeout_falls_back_to_previous_value():
    """When a hook exceeds its timeout, treat it like an exception:
    preserve previous value, continue chain."""
    registry = ToolRegistry()
    seen = _register_echo(registry)

    class SlowHook(BaseToolHook):
        name = "slow"
        timeout_seconds = 0.05  # 50 ms budget

        async def pre_tool_use(self, call, _ctx):
            await asyncio.sleep(0.2)  # exceeds budget
            return call.model_copy(update={"args": {"value": "FROM_SLOW"}})

    class FastHook(BaseToolHook):
        name = "fast"

        async def pre_tool_use(self, call, _ctx):
            return call.model_copy(update={"args": {"value": "FROM_FAST"}})

    executor = GovernedToolExecutor(
        registry=registry, tool_hooks=[SlowHook(), FastHook()]
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="echo", args={"value": "original"})]
        )
    )
    await executor.execute(_build_run_input("r_timeout"), response)
    # SlowHook timed out → its replacement ignored. FastHook ran on
    # the original call and overwrote args to FROM_FAST.
    assert seen[0]["value"] == "FROM_FAST"


@pytest.mark.asyncio
async def test_hook_within_timeout_applies_normally():
    """Hook completing under timeout works as expected."""
    registry = ToolRegistry()
    seen = _register_echo(registry)

    class FastEnough(BaseToolHook):
        name = "fast_enough"
        timeout_seconds = 0.5

        async def pre_tool_use(self, call, _ctx):
            await asyncio.sleep(0.01)
            return call.model_copy(update={"args": {"value": "DONE"}})

    executor = GovernedToolExecutor(registry=registry, tool_hooks=[FastEnough()])
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="echo", args={"value": "raw"})]
        )
    )
    await executor.execute(_build_run_input("r_under_timeout"), response)
    assert seen[0]["value"] == "DONE"


# -- backwards compat ------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_bare_value_return_still_works():
    """H15-style bare ToolCall return continues to work."""
    registry = ToolRegistry()
    seen = _register_echo(registry)

    class LegacyHook(BaseToolHook):
        name = "legacy"

        async def pre_tool_use(self, call, _ctx):
            # No HookResponse — bare ToolCall (H15 shape).
            return call.model_copy(update={"args": {"value": "LEGACY"}})

    executor = GovernedToolExecutor(registry=registry, tool_hooks=[LegacyHook()])
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="echo", args={"value": "raw"})]
        )
    )
    await executor.execute(_build_run_input("r_legacy"), response)
    assert seen[0]["value"] == "LEGACY"


@pytest.mark.asyncio
async def test_hook_response_wrong_value_type_ignored():
    """HookResponse(value=<wrong type>) ignored gracefully."""
    registry = ToolRegistry()
    seen = _register_echo(registry)

    class WrongValue(BaseToolHook):
        name = "wrong_value"

        async def pre_tool_use(self, _call, _ctx):
            return HookResponse(value="not a ToolCall")  # type: ignore[arg-type]

    executor = GovernedToolExecutor(registry=registry, tool_hooks=[WrongValue()])
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="echo", args={"value": "untouched"})]
        )
    )
    await executor.execute(_build_run_input("r_wrong_value"), response)
    assert seen[0]["value"] == "untouched"


@pytest.mark.asyncio
async def test_no_hooks_no_chain_context_metadata():
    """When no hooks contribute context, the metadata key is NOT added."""
    registry = ToolRegistry()
    _register_echo(registry)
    executor = GovernedToolExecutor(registry=registry)
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="echo", args={"value": "x"})]
        )
    )
    result = await executor.execute(_build_run_input("r_no_hooks"), response)
    assert "hook_chain_context" not in (result.envelopes[0].metadata or {})
