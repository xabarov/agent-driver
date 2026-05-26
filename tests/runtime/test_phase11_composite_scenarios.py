"""Phase 11 composite scenarios — exercises H12+H13+H15+H16 together.

These tests validate that the openclaude-derived improvements compose
cleanly without surprising interactions. Each scenario models a
realistic operator flow.
"""

from __future__ import annotations

import asyncio
import time

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
from agent_driver.contracts.hooks import BaseToolHook
from agent_driver.contracts.interrupts import (
    AllowedPrompt,
    AllowedPromptPattern,
)
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.tools import GovernedToolExecutor, ToolRegistry
from agent_driver.tools.context import report_tool_progress
from tests.runtime.conftest import llm_request_with_planned_calls


@pytest.mark.asyncio
async def test_scenario_recon_fanout_with_progress_and_hooks():
    """Scenario: agent fans out 3 read-only recon tools in parallel.

    Combines:
    * H12 — three concurrency-safe reads run via parallel batch.
    * H16 — each tool reports progress; result.progress_events captures
      all six (2 per tool, by call_index).
    * H15 — pre-hook redacts a ``target`` arg if it contains a secret;
      post-hook tags every envelope with ``audit_tag``.

    Expected wall time ≈ max(per-tool) not sum (parallel speedup).
    """
    registry = ToolRegistry()

    async def make_recon(name: str):
        async def _recon(args):
            target = args.get("target", "unknown")
            report_tool_progress(
                kind="probe", message=f"{name}:start target={target}"
            )
            await asyncio.sleep(0.05)
            report_tool_progress(
                kind="probe",
                message=f"{name}:done",
                completion_ratio=1.0,
            )
            return {"summary": f"{name}:{target}:ok"}

        return _recon

    for tool_name in ("subfinder", "ctfr", "httpx_probe"):
        registry.register(
            ToolManifest(
                name=tool_name,
                description=f"recon: {tool_name}",
                risk=ToolRisk.LOW,
                side_effect=SideEffectClass.READ_ONLY,
                approval_mode=ApprovalMode.NEVER,
                idempotent=True,
                output_char_budget=2000,
            ),
            await make_recon(tool_name),
        )

    class SecretRedactor(BaseToolHook):
        name = "secret_redactor"

        async def pre_tool_use(self, call, _ctx):
            args = dict(call.args)
            if isinstance(args.get("target"), str) and "secret" in args["target"]:
                args["target"] = "[REDACTED]"
                return call.model_copy(update={"args": args})
            return None

    class AuditTagger(BaseToolHook):
        name = "audit_tagger"

        async def post_tool_use(self, envelope, _ctx):
            metadata = dict(envelope.metadata or {})
            metadata["audit_tag"] = "recon_fanout_v1"
            return envelope.model_copy(update={"metadata": metadata})

    executor = GovernedToolExecutor(
        registry=registry,
        tool_hooks=[SecretRedactor(), AuditTagger()],
        concurrency_limit=4,
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[
                ToolCall(tool_name="subfinder", args={"target": "example.com"}),
                ToolCall(tool_name="ctfr", args={"target": "secret-target.example.com"}),
                ToolCall(tool_name="httpx_probe", args={"target": "example.com"}),
            ]
        )
    )

    started = time.perf_counter()
    result = await executor.execute(
        AgentRunInput(
            input="fanout",
            run_id="recon_fanout_run",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
        ),
        response,
    )
    elapsed = time.perf_counter() - started

    # H12: parallel speedup — 3 × 0.05 = 0.15 sequential; expect < 0.10.
    assert elapsed < 0.10, f"parallel fanout took {elapsed:.3f}s"

    # H15 pre-hook: ctfr saw [REDACTED] target.
    assert result.envelopes[1].summary == "ctfr:[REDACTED]:ok"
    # Others unchanged.
    assert result.envelopes[0].summary == "subfinder:example.com:ok"
    assert result.envelopes[2].summary == "httpx_probe:example.com:ok"

    # H15 post-hook: every envelope tagged.
    for env in result.envelopes:
        assert env.metadata.get("audit_tag") == "recon_fanout_v1"

    # H16 progress: 6 events (2 per tool), each carries call_index 1/2/3.
    assert len(result.progress_events) == 6
    by_index = {}
    for entry in result.progress_events:
        by_index.setdefault(entry.call_index, []).append(entry.progress.message)
    assert set(by_index.keys()) == {1, 2, 3}
    for messages in by_index.values():
        assert len(messages) == 2
        assert messages[0].endswith(":start target=example.com") or messages[
            0
        ].endswith(":start target=[REDACTED]")
        assert messages[1].endswith(":done")


@pytest.mark.asyncio
async def test_scenario_approved_prompt_lets_safe_write_through_under_approval_mode():
    """Scenario: operator approved 'config commits' category; ``git commit``
    runs without interrupt while ``rm -rf /`` still requires approval.

    Combines:
    * H13 — approved prompt category bypasses INTERRUPT for matching calls.
    * H15 — post-hook adds ``policy_decision_reason`` to envelope.metadata
      so audit logs show why the call ran.
    """
    registry = ToolRegistry()

    async def _shell(args):
        return {"summary": f"executed:{args.get('cmd', '')}"}

    registry.register(
        ToolManifest(
            name="risky_shell",
            description="shell — approval required by run policy",
            risk=ToolRisk.MEDIUM,
            side_effect=SideEffectClass.REVERSIBLE_WRITE,
            approval_mode=ApprovalMode.ALWAYS,
            idempotent=False,
            output_char_budget=2000,
        ),
        _shell,
    )

    class ReasonRecorder(BaseToolHook):
        name = "reason_recorder"

        async def post_tool_use(self, envelope, _ctx):
            metadata = dict(envelope.metadata or {})
            metadata["policy_reason_seen"] = "yes"
            return envelope.model_copy(update={"metadata": metadata})

    executor = GovernedToolExecutor(
        registry=registry, tool_hooks=[ReasonRecorder()]
    )
    provider = FakeProvider(response_text="ok")

    approved = [
        AllowedPrompt(
            category_id="config_commits",
            description="benign git commits",
            tool_name="risky_shell",
            arg_patterns=[
                AllowedPromptPattern(arg_name="cmd", regex=r"^git commit -m"),
            ],
        )
    ]
    run_input = AgentRunInput(
        input="commit my work",
        run_id="commit_run",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.APPROVAL_REQUIRED),
        app_metadata={"approved_prompts": [p.model_dump() for p in approved]},
    )

    # Safe call matches approved category → runs through.
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[
                ToolCall(
                    tool_name="risky_shell",
                    args={"cmd": 'git commit -m "fix typo"'},
                )
            ]
        )
    )
    result = await executor.execute(run_input, response)
    assert result.interrupt is None
    assert "executed:" in result.envelopes[0].summary
    # H15 post-hook ran.
    assert result.envelopes[0].metadata.get("policy_reason_seen") == "yes"

    # Dangerous call doesn't match → still requires approval.
    response2 = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="risky_shell", args={"cmd": "rm -rf /"})]
        )
    )
    result2 = await executor.execute(run_input, response2)
    assert result2.interrupt is not None


@pytest.mark.asyncio
async def test_scenario_parallel_batch_with_one_write_in_middle():
    """Scenario: 5 calls — 2 reads, then 1 write, then 2 reads.

    Partition: parallel(r1, r2) → serial(w1) → parallel(r3, r4).
    Verifies all H12 contract (parallel speedup + serial writes) plus
    H16 progress correlation across batches.
    """
    registry = ToolRegistry()

    async def make_handler(name: str, delay: float):
        async def _h(_args):
            report_tool_progress(kind="phase", message=f"{name}:enter")
            await asyncio.sleep(delay)
            report_tool_progress(kind="phase", message=f"{name}:exit")
            return {"summary": name}

        return _h

    for r in ("r1", "r2", "r3", "r4"):
        registry.register(
            ToolManifest(
                name=r,
                description="read",
                risk=ToolRisk.LOW,
                side_effect=SideEffectClass.READ_ONLY,
                approval_mode=ApprovalMode.NEVER,
                idempotent=True,
                output_char_budget=2000,
            ),
            await make_handler(r, 0.05),
        )
    registry.register(
        ToolManifest(
            name="w1",
            description="write",
            risk=ToolRisk.MEDIUM,
            side_effect=SideEffectClass.REVERSIBLE_WRITE,
            approval_mode=ApprovalMode.NEVER,
            idempotent=False,
            output_char_budget=2000,
        ),
        await make_handler("w1", 0.05),
    )

    executor = GovernedToolExecutor(registry=registry)
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[
                ToolCall(tool_name=n, args={})
                for n in ("r1", "r2", "w1", "r3", "r4")
            ]
        )
    )
    started = time.perf_counter()
    result = await executor.execute(
        AgentRunInput(
            input="mixed",
            run_id="mixed_run",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
        ),
        response,
    )
    elapsed = time.perf_counter() - started

    # Expected: parallel(r1,r2) + serial(w1) + parallel(r3,r4) ≈ 3 × 0.05.
    # Sequential would be 5 × 0.05.
    assert elapsed < 0.20

    # Order preserved.
    assert [t.tool_name for t in result.traces] == ["r1", "r2", "w1", "r3", "r4"]

    # H16: 10 progress events (2 per tool), all correlated to call_index.
    assert len(result.progress_events) == 10
    by_index = {}
    for entry in result.progress_events:
        by_index.setdefault(entry.call_index, []).append(entry.progress.message)
    assert set(by_index.keys()) == {1, 2, 3, 4, 5}
    for idx, messages in by_index.items():
        assert len(messages) == 2
        assert messages[0].endswith(":enter")
        assert messages[1].endswith(":exit")
