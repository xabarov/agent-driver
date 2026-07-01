"""Recovery path for disallowed management tool calls in scoped workflow nodes.

A scoped node restricts ``allowed_tools`` to real executable tools but a model
can still emit an out-of-schema management call (``todo_write`` …). These tests
cover the distinct ``disallowed_management_tool`` denial class: structured repair
metadata, a recovery hint, NodeContract tool-use progress that ignores the
denial, and backwards-compatible behaviour when management tools are allowed.
"""

from __future__ import annotations

import pytest

from agent_driver.contracts import (
    AgentRunInput,
    ApprovalMode,
    ChatMessage,
    NodeContract,
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
from agent_driver.runtime.single_agent.llm_step.build import (
    effective_tool_names_from_registry,
)
from agent_driver.tools import GovernedToolExecutor, ToolRegistry
from tests.runtime.conftest import llm_request_with_planned_calls

_USAGE = UsageSummary(model_provider="fake", model_name="test-model")


def _exec_manifest(name: str) -> ToolManifest:
    return ToolManifest(
        name=name,
        description=f"executable tool {name}",
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.READ_ONLY,
        approval_mode=ApprovalMode.NEVER,
        idempotent=True,
        output_char_budget=2000,
    )


def _registry(*names: str) -> ToolRegistry:
    registry = ToolRegistry()
    for name in names:

        async def _handler(args, _name=name):
            return {"summary": f"{_name} ran", "target": args.get("target")}

        registry.register(_exec_manifest(name), _handler)
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


def _scoped_run_input(
    *, allowed=("httpx", "nmap"), node_contract: NodeContract | None = None, **kw
) -> AgentRunInput:
    return AgentRunInput(
        input="run the scan for example input X",
        run_id=kw.pop("run_id", "run_mgmt"),
        agent_id="agent",
        graph_preset="single_react",
        max_steps=kw.pop("max_steps", 12),
        max_tool_calls=kw.pop("max_tool_calls", 6),
        tool_policy=ToolPolicyInput(
            mode=ToolPolicyMode.ALLOW_TOOLS,
            allowed_tools=list(allowed) if allowed is not None else None,
        ),
        node_contract=node_contract or NodeContract(),
    )


def _tool_call_response(tool_name: str, args: dict) -> LlmResponse:
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
                    args=args,
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


# --- Executor: distinct denial class with structured metadata ------------------
@pytest.mark.asyncio
async def test_disallowed_management_tool_denied_with_structured_metadata() -> None:
    """A management call absent from allowed_tools yields a typed repair envelope."""
    executor = GovernedToolExecutor(registry=_registry("httpx", "nmap"))
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="todo_write", args={"todos": []})]
        )
    )

    result = await executor.execute(_scoped_run_input(run_id="run_exec_deny"), response)

    envelope = result.envelopes[0]
    assert envelope.decision.value == "deny"
    assert envelope.error is not None and envelope.error.code == "policy_denied"
    structured = envelope.structured_output
    assert structured is not None
    assert structured["error_kind"] == "disallowed_management_tool"
    assert structured["blocked_tool"] == "todo_write"
    assert structured["allowed_tools"] == ["httpx", "nmap"]
    assert structured["retry_expected"] is True
    assert result.traces[0].status.value == "denied"


# --- Full run: denied todo_write recovers into a real tool call ----------------
class _TodoThenHttpxProvider(FakeProvider):
    """Turn 1 calls todo_write (denied); turn 2 calls httpx; turn 3 answers."""

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
            return _tool_call_response("todo_write", {"todos": [{"title": "scan"}]})
        if self.calls == 2:
            return _tool_call_response("httpx", {"target": "example-input-x"})
        return _text_response("scan complete: results attached")


@pytest.mark.asyncio
async def test_scoped_run_recovers_from_denied_todo_write_and_calls_tool() -> None:
    """A denied todo_write yields a repair hint, then httpx executes and finalizes."""
    provider = _TodoThenHttpxProvider()
    runner = _runner(_registry("httpx", "nmap"), provider)

    output = await runner.run(_scoped_run_input(run_id="run_recover"))

    assert output.status.value == "completed"
    # The assigned executable tool actually ran.
    assert any(
        t.tool_name == "httpx" and t.status.value == "completed"
        for t in output.tool_trace
    )
    # The final answer is real task evidence, not a "cannot execute tools" giveup.
    assert output.answer == "scan complete: results attached"
    assert "cannot" not in (output.answer or "").lower()
    # The repair hint reached the model on the turn after the denial.
    assert any(
        "not available for this run" in text and "httpx" in text
        for text in provider.user_texts
    )


# --- NodeContract: a denied management call is not tool-use progress ------------
class _TodoThenRefuseProvider(FakeProvider):
    """Turn 1 calls todo_write (denied); thereafter refuses in prose."""

    def __init__(self) -> None:
        super().__init__(response_text="unused")
        self.calls = 0

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.calls += 1
        if self.calls == 1:
            return _tool_call_response("todo_write", {"todos": []})
        return _text_response("I cannot execute tools; here are instructions.")


@pytest.mark.asyncio
async def test_denied_todo_write_does_not_satisfy_require_tool_use() -> None:
    """require_tool_use must not be satisfied by a denied management call alone."""
    provider = _TodoThenRefuseProvider()
    runner = _runner(_registry("httpx", "nmap"), provider)

    output = await runner.run(
        _scoped_run_input(
            run_id="run_contract",
            node_contract=NodeContract(require_tool_use=True, max_tool_use_reprompts=1),
        )
    )

    summary = output.metadata["node_contract"]
    # The denied todo_write did not count as tool-use progress, so the run was
    # reprompted and then stamped a typed violation rather than completing clean.
    assert summary["reprompts"] >= 1
    violation = summary["violation"]
    assert violation is not None
    assert violation["kind"] == "no_tool_use"


# --- Backwards compat: an allowed management tool still executes ----------------
@pytest.mark.asyncio
async def test_allowed_management_tool_executes_normally() -> None:
    """A chat/planning run that allows todo_write keeps executing it (no repair)."""
    registry = ToolRegistry()

    async def _todo(args):
        return {"summary": "todos updated", "count": len(args.get("todos", []))}

    registry.register(_exec_manifest("todo_write"), _todo)
    executor = GovernedToolExecutor(registry=registry)
    run_input = AgentRunInput(
        input="plan the work",
        run_id="run_allowed_mgmt",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(
            mode=ToolPolicyMode.ALLOW_TOOLS,
            allowed_tools=["todo_write"],
        ),
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="todo_write", args={"todos": [{"t": "a"}]})]
        )
    )

    result = await executor.execute(run_input, response)

    envelope = result.envelopes[0]
    assert envelope.error is None
    assert envelope.decision.value == "allow"
    assert result.traces[0].status.value == "completed"
    structured = envelope.structured_output or {}
    assert structured.get("error_kind") != "disallowed_management_tool"


# --- Request schema filtering remains unchanged --------------------------------
def test_disallowed_management_tool_excluded_from_request_tools() -> None:
    """Management tools absent from allowed_tools must not surface in provider tools."""
    registry = _registry("httpx", "nmap")

    async def _todo(args):
        return {"summary": "ok"}

    registry.register(_exec_manifest("todo_write"), _todo)

    names = effective_tool_names_from_registry(
        registry, allowed=("httpx", "nmap"), denied=None
    )
    assert set(names) == {"httpx", "nmap"}
    assert "todo_write" not in names
