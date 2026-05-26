"""Phase 11 H15 — tests for pre/post tool-use hooks.

Pins the contract:
* pre_tool_use may transform ``ToolCall.args`` (or return None for no
  change); transformed call drives policy + execution.
* post_tool_use may transform the result envelope (e.g. add metadata).
* Multiple hooks run in registration order; each sees previous output.
* Hook exceptions are isolated — chain falls back to pre-hook value
  for that hook, continues with next hook.
* Returning a wrong type (not ToolCall / ToolResultEnvelope) is
  ignored gracefully (deduplicated warning, no crash).
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_driver.contracts import (
    AgentRunInput,
    ApprovalMode,
    SideEffectClass,
    ToolCall,
    ToolManifest,
    ToolPolicyInput,
    ToolPolicyMode,
    ToolResultEnvelope,
    ToolRisk,
)
from agent_driver.contracts.hooks import BaseToolHook, ToolHook
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


def _register_echo(registry: ToolRegistry, name: str = "echo") -> list[dict[str, Any]]:
    """Register a tool that records the args it received (for pre-hook
    assertions) and echoes them back in summary."""
    seen: list[dict[str, Any]] = []

    async def _echo(args):
        seen.append(dict(args))
        return {"summary": f"echoed:{args.get('value', '')}"}

    registry.register(
        ToolManifest(
            name=name,
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


@pytest.mark.asyncio
async def test_pre_hook_transforms_args_before_handler_runs():
    """Hook redacts a sensitive value; handler sees the redacted form."""
    registry = ToolRegistry()
    seen = _register_echo(registry)

    class Redactor(BaseToolHook):
        name = "redactor"

        async def pre_tool_use(self, call, _ctx):
            args = dict(call.args)
            if isinstance(args.get("value"), str) and args["value"].startswith("sk-"):
                args["value"] = "REDACTED"
            return call.model_copy(update={"args": args})

    executor = GovernedToolExecutor(registry=registry, tool_hooks=[Redactor()])
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="echo", args={"value": "sk-secret-abc"})]
        )
    )
    result = await executor.execute(_build_run_input("run_redact"), response)

    assert len(seen) == 1
    assert seen[0]["value"] == "REDACTED"
    assert result.envelopes[0].summary == "echoed:REDACTED"


@pytest.mark.asyncio
async def test_post_hook_enriches_envelope_metadata():
    """Hook adds app_trace_id to envelope metadata after handler runs."""
    registry = ToolRegistry()
    _register_echo(registry)

    class TraceTagger(BaseToolHook):
        name = "trace_tagger"

        async def post_tool_use(self, envelope, _ctx):
            metadata = dict(envelope.metadata or {})
            metadata["app_trace_id"] = "trace-xyz"
            return envelope.model_copy(update={"metadata": metadata})

    executor = GovernedToolExecutor(registry=registry, tool_hooks=[TraceTagger()])
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="echo", args={"value": "v"})]
        )
    )
    result = await executor.execute(_build_run_input("run_trace"), response)

    assert result.envelopes[0].metadata.get("app_trace_id") == "trace-xyz"


@pytest.mark.asyncio
async def test_hooks_chain_in_registration_order():
    """Two hooks: first replaces value to 'A', second replaces to 'AB'.
    Handler sees the final 'AB' (chain respects order)."""
    registry = ToolRegistry()
    seen = _register_echo(registry)

    class SetA(BaseToolHook):
        name = "set_a"

        async def pre_tool_use(self, call, _ctx):
            return call.model_copy(update={"args": {**call.args, "value": "A"}})

    class AppendB(BaseToolHook):
        name = "append_b"

        async def pre_tool_use(self, call, _ctx):
            return call.model_copy(
                update={"args": {**call.args, "value": call.args["value"] + "B"}}
            )

    executor = GovernedToolExecutor(registry=registry, tool_hooks=[SetA(), AppendB()])
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="echo", args={"value": "x"})]
        )
    )
    await executor.execute(_build_run_input("run_chain"), response)
    assert seen[0]["value"] == "AB"


@pytest.mark.asyncio
async def test_pre_hook_returning_none_passes_through():
    """None means no change — next hook / handler sees unmodified value."""
    registry = ToolRegistry()
    seen = _register_echo(registry)

    class NoChange(BaseToolHook):
        name = "no_change"

        async def pre_tool_use(self, _call, _ctx):
            return None  # explicit no-op

    executor = GovernedToolExecutor(registry=registry, tool_hooks=[NoChange()])
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="echo", args={"value": "raw"})]
        )
    )
    await executor.execute(_build_run_input("run_noop"), response)
    assert seen[0]["value"] == "raw"


@pytest.mark.asyncio
async def test_pre_hook_exception_isolated_from_chain():
    """Hook A raises → chain continues with hook B; B's input is the
    pre-A value (no propagation of the bad transform)."""
    registry = ToolRegistry()
    seen = _register_echo(registry)

    class BadHook(BaseToolHook):
        name = "bad"

        async def pre_tool_use(self, _call, _ctx):
            raise RuntimeError("explode")

    class GoodHook(BaseToolHook):
        name = "good"

        async def pre_tool_use(self, call, _ctx):
            return call.model_copy(update={"args": {"value": "from_good"}})

    executor = GovernedToolExecutor(
        registry=registry, tool_hooks=[BadHook(), GoodHook()]
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="echo", args={"value": "original"})]
        )
    )
    await executor.execute(_build_run_input("run_iso"), response)
    # BadHook raised → its replacement is ignored. GoodHook runs on
    # the original call and writes "from_good". Handler sees that.
    assert seen[0]["value"] == "from_good"


@pytest.mark.asyncio
async def test_pre_hook_returning_wrong_type_is_ignored():
    """Hook returns a str instead of ToolCall → warning logged, ignored."""
    registry = ToolRegistry()
    seen = _register_echo(registry)

    class WrongType(BaseToolHook):
        name = "wrong_type"

        async def pre_tool_use(self, _call, _ctx):
            return "not a ToolCall"  # type: ignore[return-value]

    executor = GovernedToolExecutor(registry=registry, tool_hooks=[WrongType()])
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="echo", args={"value": "untouched"})]
        )
    )
    await executor.execute(_build_run_input("run_wrong"), response)
    assert seen[0]["value"] == "untouched"


@pytest.mark.asyncio
async def test_post_hook_exception_preserves_original_envelope():
    """Bad post-hook → original envelope reaches result.envelopes."""
    registry = ToolRegistry()
    _register_echo(registry)

    class BadPost(BaseToolHook):
        name = "bad_post"

        async def post_tool_use(self, _envelope, _ctx):
            raise RuntimeError("kaput")

    executor = GovernedToolExecutor(registry=registry, tool_hooks=[BadPost()])
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="echo", args={"value": "hello"})]
        )
    )
    result = await executor.execute(_build_run_input("run_bad_post"), response)
    # Handler ran successfully; envelope preserved.
    assert result.envelopes[0].summary == "echoed:hello"


@pytest.mark.asyncio
async def test_no_hooks_registered_is_baseline_unchanged():
    """Empty tool_hooks (default) → executor behaves exactly as before."""
    registry = ToolRegistry()
    seen = _register_echo(registry)

    executor = GovernedToolExecutor(registry=registry)  # no hooks
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="echo", args={"value": "baseline"})]
        )
    )
    result = await executor.execute(_build_run_input("run_baseline"), response)
    assert seen[0]["value"] == "baseline"
    assert result.envelopes[0].summary == "echoed:baseline"


def test_tool_hook_protocol_runtime_checkable():
    """A class with both methods satisfies the ToolHook Protocol."""

    class MyHook(BaseToolHook):
        name = "my_hook"

    instance = MyHook()
    assert isinstance(instance, ToolHook)
