"""Runtime enforcement tests for AgentRunInput.node_contract (Layers A/B/C).

Covers the acceptance criteria for reliable tool-first workflow nodes plus the
early-finalize-from-tool-evidence escape hatch. All scenarios drive the full
SingleAgentRunner loop with a scripted FakeProvider and a small custom tool
registry (lookup_a / lookup_b) so behaviour is asserted end-to-end.
"""

from __future__ import annotations

import pytest

from agent_driver.contracts import (
    AgentRunInput,
    ApprovalMode,
    ChatMessage,
    FinalizeNow,
    NodeContract,
    RuntimeEventType,
    SideEffectClass,
    ToolCall,
    ToolManifest,
    ToolPolicyInput,
    ToolPolicyMode,
    ToolRisk,
)
from agent_driver.llm.contracts import (
    LlmFinishReason,
    LlmRequest,
    LlmResponse,
    UsageSummary,
)
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
    wrap_governed_executor,
)
from agent_driver.runtime.lifecycle_hooks import BaseRunLifecycleHook
from agent_driver.tools import GovernedToolExecutor, ToolRegistry

_USAGE = UsageSummary(model_provider="fake", model_name="test-model")


def _lookup_manifest(name: str) -> ToolManifest:
    return ToolManifest(
        name=name,
        description=f"lookup tool {name}",
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.READ_ONLY,
        approval_mode=ApprovalMode.NEVER,
        idempotent=True,
        output_char_budget=2000,
    )


def _build_registry(*names: str) -> ToolRegistry:
    registry = ToolRegistry()
    for name in names:

        async def _handler(args, _name=name):
            target = args.get("target", "unknown")
            return {
                "summary": f"{_name} ran for {target}",
                "results": [f"{_name}-1.{target}", f"{_name}-2.{target}"],
            }

        registry.register(_lookup_manifest(name), _handler)
    return registry


def _runner(registry: ToolRegistry, provider: FakeProvider, **config_kwargs):
    return FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            tool_registry=registry,
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            ),
            **config_kwargs,
        ),
    )


def _tool_call_response(tool_name: str, *, target: str) -> LlmResponse:
    return LlmResponse(
        message=ChatMessage(role="assistant", content=""),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=_USAGE,
        provider="fake",
        model="test-model",
        metadata={
            "planned_tool_calls": [
                ToolCall(
                    tool_name=tool_name,
                    tool_call_id=f"call_{tool_name}",
                    args={"target": target},
                ).model_dump(mode="json")
            ]
        },
    )


def _text_response(text: str) -> LlmResponse:
    return LlmResponse(
        message=ChatMessage(role="assistant", content=text),
        finish_reason=LlmFinishReason.STOP,
        usage=_USAGE,
        provider="fake",
        model="test-model",
        metadata={},
    )


def _run_input(node_contract: NodeContract, *, allowed=("lookup_a", "lookup_b"), **kw):
    return AgentRunInput(
        input="run lookup for example input X",
        run_id=kw.pop("run_id", "run_nc"),
        agent_id="agent",
        graph_preset="single_react",
        max_steps=kw.pop("max_steps", 12),
        max_tool_calls=kw.pop("max_tool_calls", 6),
        tool_policy=ToolPolicyInput(
            mode=ToolPolicyMode.ALLOW_TOOLS,
            allowed_tools=list(allowed) if allowed is not None else None,
        ),
        node_contract=node_contract,
        **kw,
    )


# --- Layer B: reactive reprompt recovers into a real tool call -----------------
class _ProseThenToolProvider(FakeProvider):
    """Turn 1 refuses in prose; turn 2 calls a tool; turn 3 answers."""

    def __init__(self) -> None:
        super().__init__(response_text="unused")
        self.calls = 0
        self.system_texts: list[str] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.calls += 1
        self.system_texts.append(
            " ".join(m.content for m in request.messages if m.role == "system")
        )
        if self.calls == 1:
            return _text_response("I don't have access to tools.")
        if self.calls == 2:
            return _tool_call_response("lookup_a", target="example-input-x")
        return _text_response("final answer with results")


@pytest.mark.asyncio
async def test_zero_tool_finalize_reprompts_then_calls_tool() -> None:
    """A no-tool refusal under require_tool_use is recovered into a real call."""
    provider = _ProseThenToolProvider()
    runner = _runner(_build_registry("lookup_a", "lookup_b"), provider)
    output = await runner.run(
        _run_input(NodeContract(require_tool_use=True), run_id="run_reprompt")
    )
    assert output.status.value == "completed"
    assert output.metadata["node_contract"]["tool_calls"] >= 1
    assert any(t.tool_name == "lookup_a" for t in output.tool_trace)
    summary = output.metadata["node_contract"]
    assert summary["reprompts"] >= 1
    assert summary["violation"] is None
    assert output.answer == "final answer with results"


# --- Layer B: persistent refusal escalates to a structured violation -----------
class _AlwaysProseProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__(response_text="unused")
        self.calls = 0

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.calls += 1
        return _text_response("I cannot access tools; here are instructions instead.")


@pytest.mark.asyncio
async def test_persistent_no_tool_use_stamps_recoverable_violation() -> None:
    """Repeated refusal is classified as a typed violation, not a silent answer."""
    provider = _AlwaysProseProvider()
    runner = _runner(_build_registry("lookup_a", "lookup_b"), provider)
    output = await runner.run(
        _run_input(
            NodeContract(require_tool_use=True, max_tool_use_reprompts=1),
            run_id="run_violation",
        )
    )
    assert output.status.value == "completed"
    assert output.metadata["node_contract"]["tool_calls"] == 0
    violation = output.metadata["node_contract"]["violation"]
    assert violation is not None
    assert violation["kind"] == "no_tool_use"
    assert violation["reprompts"] == 1
    # One initial finalize + one reprompt → exactly two LLM turns.
    assert provider.calls == 2


# --- Layer B: proactive prelude reaches the system prompt ----------------------
class _ImmediateToolProvider(FakeProvider):
    def __init__(self, tool_name: str = "lookup_a") -> None:
        super().__init__(response_text="unused")
        self.calls = 0
        self.system_texts: list[str] = []
        self._tool_name = tool_name

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.calls += 1
        self.system_texts.append(
            " ".join(m.content for m in request.messages if m.role == "system")
        )
        if self.calls == 1:
            return _tool_call_response(self._tool_name, target="example-input-x")
        return _text_response("done")


class _DiscoveryThenRequiredToolProvider(FakeProvider):
    """Calls discovery, tries to finalize, then obeys the required-tool reprompt."""

    def __init__(self) -> None:
        super().__init__(response_text="unused")
        self.calls = 0
        self.user_texts: list[str] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.calls += 1
        self.user_texts.append(
            " ".join(m.content for m in request.messages if m.role == "user")
        )
        if self.calls == 1:
            return _tool_call_response("lookup_a", target="example-input-x")
        if self.calls == 2:
            return _text_response("I found lookup_a results; finalizing now.")
        return _tool_call_response("lookup_b", target="example-input-x")


@pytest.mark.asyncio
async def test_prelude_injected_into_system_prompt() -> None:
    """The proactive prelude names the callable tools + target on turn 1."""
    provider = _ImmediateToolProvider()
    runner = _runner(_build_registry("lookup_a", "lookup_b"), provider)
    await runner.run(
        _run_input(
            NodeContract(
                require_tool_use=True, target="culmen.com", task_hint="enumerate"
            ),
            run_id="run_prelude",
        )
    )
    first_system = provider.system_texts[0]
    assert "lookup_a" in first_system
    assert "culmen.com" in first_system
    assert "tool-using workflow node" in first_system


@pytest.mark.asyncio
async def test_required_completed_tool_reprompts_before_final_answer() -> None:
    """A discovery-only tool call does not satisfy a required terminal tool."""
    provider = _DiscoveryThenRequiredToolProvider()
    runner = _runner(_build_registry("lookup_a", "lookup_b"), provider)
    output = await runner.run(
        _run_input(
            NodeContract(
                require_tool_use=True,
                require_completed_tools=["lookup_b"],
                finalize_when_tools=["lookup_b"],
                max_tool_use_reprompts=1,
            ),
            run_id="run_required_tool",
        )
    )

    assert output.status.value == "completed"
    assert provider.calls == 3
    assert any(t.tool_name == "lookup_a" for t in output.tool_trace)
    assert any(t.tool_name == "lookup_b" for t in output.tool_trace)
    summary = output.metadata["node_contract"]
    assert summary["require_completed_tools"] == ["lookup_b"]
    assert summary["reprompts"] == 1
    assert summary["violation"] is None
    assert summary["early_finalize_reason"] == "finalize_when_tools_satisfied"
    assert "lookup_b" in output.answer
    assert "required tool(s) have not completed successfully" in provider.user_texts[-1]


@pytest.mark.asyncio
async def test_missing_required_completed_tool_stamps_violation() -> None:
    """Persistent premature finalization is a typed missing-required-tools violation."""
    provider = _AlwaysProseProvider()
    runner = _runner(_build_registry("lookup_a", "lookup_b"), provider)
    output = await runner.run(
        _run_input(
            NodeContract(
                require_completed_tools=["lookup_b"],
                max_tool_use_reprompts=1,
            ),
            run_id="run_missing_required_tool",
        )
    )

    violation = output.metadata["node_contract"]["violation"]
    assert violation["kind"] == "missing_required_tools"
    assert violation["missing_tools"] == ["lookup_b"]
    assert violation["reprompts"] == 1
    assert provider.calls == 2


# --- Layer A: policy↔registry mismatch surfaces a structured warning -----------
@pytest.mark.asyncio
async def test_layer_a_warns_on_unsatisfiable_allowed_tool() -> None:
    """An allowed tool absent from the registry is flagged, not silently dropped."""
    provider = _ImmediateToolProvider()
    runner = _runner(_build_registry("lookup_a"), provider)
    output = await runner.run(
        _run_input(
            NodeContract(require_callable_tools=True),
            allowed=["lookup_a", "missing_tool"],
            run_id="run_layer_a",
        )
    )
    warnings = output.metadata["node_contract"]["tool_policy_warnings"]
    assert warnings == ["missing_tool"]
    warn_events = [
        e for e in output.events if e.type == RuntimeEventType.NODE_CONTRACT_WARNING
    ]
    assert warn_events
    assert warn_events[0].payload["tools"] == ["missing_tool"]


# --- Layer C: declarative finalize_when_tools skips the extra LLM pass ----------
@pytest.mark.asyncio
async def test_finalize_when_tools_skips_extra_llm_continuation() -> None:
    """Once the declared tool succeeds, the run finalizes with no second LLM call."""
    provider = _ImmediateToolProvider()
    runner = _runner(_build_registry("lookup_a", "lookup_b"), provider)
    output = await runner.run(
        _run_input(
            NodeContract(finalize_when_tools=["lookup_a"]),
            run_id="run_finalize_when",
        )
    )
    assert output.status.value == "completed"
    assert provider.calls == 1  # no continuation after sufficient tool evidence
    assert output.metadata["node_contract"]["tool_calls"] == 1
    summary = output.metadata["node_contract"]
    assert summary["early_finalize_reason"] == "finalize_when_tools_satisfied"
    assert any(
        row["tool_name"] == "lookup_a" and row["status"] == "completed"
        for row in summary["executed_tools"]
    )
    assert output.answer and "lookup_a" in output.answer


# --- Layer C: on_tool_evidence host hook finalizes now --------------------------
class _StopAfterEvidenceHook(BaseRunLifecycleHook):
    name = "stop_after_tool_evidence"

    async def on_tool_evidence(self, context, envelopes):
        if envelopes:
            return FinalizeNow(answer="finalized-by-host-hook")
        return None


@pytest.mark.asyncio
async def test_on_tool_evidence_hook_finalizes_without_continuation() -> None:
    """A host hook can finalize from tool evidence with its own answer."""
    provider = _ImmediateToolProvider()
    registry = _build_registry("lookup_a", "lookup_b")
    runner = _runner(registry, provider, lifecycle_hooks=(_StopAfterEvidenceHook(),))
    output = await runner.run(
        _run_input(NodeContract(require_tool_use=True), run_id="run_hook")
    )
    assert output.status.value == "completed"
    assert provider.calls == 1
    assert output.answer == "finalized-by-host-hook"
    assert (
        output.metadata["node_contract"]["early_finalize_reason"]
        == "tool_evidence_satisfies_contract"
    )


# --- Stream events: stable tool-call shape for downstream normalization ---------
@pytest.mark.asyncio
async def test_tool_call_completed_event_carries_stable_fields() -> None:
    """TOOL_CALL_COMPLETED rows expose tool_name/id/status/output_preview/payload."""
    provider = _ImmediateToolProvider()
    runner = _runner(_build_registry("lookup_a", "lookup_b"), provider)
    output = await runner.run(
        _run_input(NodeContract(finalize_when_tools=["lookup_a"]), run_id="run_events")
    )
    completed = [
        e for e in output.events if e.type == RuntimeEventType.TOOL_CALL_COMPLETED
    ]
    assert completed
    row = completed[0].payload["tools"][0]
    assert row["tool_name"] == "lookup_a"
    assert row["tool_call_id"] == "call_lookup_a"
    assert row["status"] == "completed"
    assert isinstance(row["output_preview"], str) and row["output_preview"]
    assert isinstance(row["structured_output"], dict)
    assert row["structured_output"]["results"]


# --- Safety: a run without a node contract is byte-for-byte unchanged -----------
@pytest.mark.asyncio
async def test_inactive_node_contract_is_inert() -> None:
    """No node_contract metadata is emitted and the loop behaves normally."""
    provider = _ImmediateToolProvider()
    runner = _runner(_build_registry("lookup_a", "lookup_b"), provider)
    output = await runner.run(_run_input(NodeContract(), run_id="run_inert"))
    assert output.status.value == "completed"
    assert "node_contract" not in output.metadata
