"""Condenser-pipeline compaction seam (Option-B1b / compaction hardening C2).

The opt-in ``use_condenser_pipeline`` flag routes transcript compaction through the
cost-ordered model-free pipeline (tool-clear → tool-history → partial). Its point is
to skip the expensive LLM summary whenever clearing old tool bulk already fits; only
when the model-free tiers fall short does the stage delegate to the mature
``_apply_llm_full_compaction`` path.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from agent_driver.context.compaction import CompactionOrchestrator
from agent_driver.contracts import (
    AgentRunInput,
    CompactionDecision,
    CompactionMode,
)
from agent_driver.contracts.enums import ChatRole, RuntimeEventType
from agent_driver.contracts.messages import ChatMessage
from agent_driver.runtime.single_agent.context_management import compaction_stage
from agent_driver.runtime.single_agent.context_management.compaction_stage import (
    _run_condenser_pipeline_dispatch,
)
from agent_driver.runtime.single_agent.types import EventSpec, RunContext


class _Host:
    def __init__(self, *, enable_llm_compaction: bool, ptl_retry_max_chars: int) -> None:
        self.events: list[EventSpec] = []
        self._config = SimpleNamespace(
            use_condenser_pipeline=True,
            enable_llm_compaction=enable_llm_compaction,
            post_compact_max_reinjected_artifact_refs=5,
            ptl_retry_max_chars=ptl_retry_max_chars,
        )

    def _emit(self, event: EventSpec) -> None:
        self.events.append(event)


def _context() -> RunContext:
    run_input = AgentRunInput(
        input="task",
        run_id="run_cp_1",
        agent_id="agent",
        graph_preset="single_react",
    )
    return RunContext(
        run_input=run_input,
        identifiers={"run_id": "run_cp_1", "attempt_id": "att_1"},
        metadata={},
    )


def _decision() -> CompactionDecision:
    return CompactionDecision(eligible=True, mode=CompactionMode.PARTIAL, metadata={})


def _tool_heavy_messages(n_tools: int, size: int) -> list[ChatMessage]:
    msgs = [ChatMessage(role=ChatRole.SYSTEM, content="policy")]
    for i in range(n_tools):
        msgs.append(
            ChatMessage(role=ChatRole.ASSISTANT, content=f"call {i}")
        )
        msgs.append(
            ChatMessage(role=ChatRole.TOOL, content="R" * size, tool_call_id=f"tc{i}")
        )
    msgs.append(ChatMessage(role=ChatRole.USER, content="next?"))
    return msgs


def _run(host, request, orchestrator) -> bool:
    orchestrator.start_attempt()
    return asyncio.run(
        _run_condenser_pipeline_dispatch(
            host,
            context=_context(),
            request=request,
            orchestrator=orchestrator,
            decision=_decision(),
            compaction_id="cmp_cp",
            circuit_breaker_open_before=False,
        )
    )


def _outcome(host: _Host) -> dict:
    events = [
        e for e in host.events if e.event_type is RuntimeEventType.MEMORY_COMPACTED
    ]
    assert events, "expected a MEMORY_COMPACTED outcome"
    return dict(events[-1].payload or {})


def test_deterministic_fit_skips_the_llm_tier(monkeypatch) -> None:
    """When clearing old tool bulk already fits the target, the LLM tier is never
    invoked — the whole point of the pipeline."""

    async def _boom(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("llm_full must not run when model-free tiers fit")

    monkeypatch.setattr(compaction_stage, "_apply_llm_full_compaction", _boom)
    # 6 tool results × 1000 chars ≈ 6000; clearing the 3 oldest frees ~3000 → under
    # a 4000-char target, so the pruner tier alone fits.
    host = _Host(enable_llm_compaction=True, ptl_retry_max_chars=4000)
    orchestrator = CompactionOrchestrator()
    request = SimpleNamespace(messages=_tool_heavy_messages(n_tools=6, size=1000))
    before = len(request.messages)

    handled = _run(host, request, orchestrator)

    assert handled is True
    payload = _outcome(host)
    assert payload["outcome"] == "successful"
    assert payload["mode"] == "condenser_pipeline"
    assert payload["fit"] is True
    assert payload["chars_freed"] > 0
    assert "tool_result_pruner" in payload["tiers"]
    # Structure preserved: same message count (tool bodies cleared, not dropped).
    assert len(request.messages) == before


def test_no_progress_without_llm_is_neutral_skip() -> None:
    """A small transcript the model-free tiers cannot shrink, with no LLM tier, is a
    neutral skip: View untouched, breaker neither reset nor advanced."""
    host = _Host(enable_llm_compaction=False, ptl_retry_max_chars=10)
    orchestrator = CompactionOrchestrator(failure_limit=3)
    # Drive one failure so a false 'success' reset would show as 0.
    from agent_driver.contracts import CompactionResult

    orchestrator.complete_attempt(
        decision=_decision(),
        result=CompactionResult(
            compaction_id="prev", mode=CompactionMode.LLM_FULL, success=False
        ),
    )
    assert orchestrator.state_snapshot()["consecutive_failures"] == 1
    original = [
        ChatMessage(role=ChatRole.SYSTEM, content="policy"),
        ChatMessage(role=ChatRole.USER, content="hi"),
    ]
    request = SimpleNamespace(messages=list(original))

    handled = _run(host, request, orchestrator)

    assert handled is True
    assert [m.content for m in request.messages] == [m.content for m in original]
    payload = _outcome(host)
    assert payload["outcome"] == "skipped"
    assert payload["skip_reason"] == "insufficient_progress"
    # Neutral: breaker not falsely reset.
    assert orchestrator.state_snapshot()["consecutive_failures"] == 1


def test_not_fit_with_llm_delegates_to_llm_full(monkeypatch) -> None:
    """When the model-free tiers cannot reach the target and the LLM tier is enabled,
    the stage delegates to the mature llm_full path (request still original)."""
    calls: list[int] = []

    async def _record(*args, **kwargs):
        calls.append(len(kwargs["request"].messages))
        return True

    monkeypatch.setattr(compaction_stage, "_apply_llm_full_compaction", _record)
    # Small transcript the model-free tiers can't shrink under a tiny target.
    host = _Host(enable_llm_compaction=True, ptl_retry_max_chars=5)
    orchestrator = CompactionOrchestrator()
    original = [
        ChatMessage(role=ChatRole.SYSTEM, content="policy"),
        ChatMessage(role=ChatRole.USER, content="a longer user turn " * 5),
    ]
    request = SimpleNamespace(messages=list(original))

    handled = _run(host, request, orchestrator)

    assert handled is True
    assert calls, "expected delegation to _apply_llm_full_compaction"
    # Delegated with the ORIGINAL request (model-free reduction not applied first).
    assert calls[0] == len(original)


def test_flag_off_uses_legacy_partial_path() -> None:
    """With the flag off the dispatcher takes the legacy mode tree, not the pipeline."""
    from agent_driver.runtime.single_agent.context_management.compaction_stage import (
        _run_compaction_mode_dispatch,
    )

    host = _Host(enable_llm_compaction=False, ptl_retry_max_chars=4000)
    host._config.use_condenser_pipeline = False
    host._config.enable_partial_compaction = True
    orchestrator = CompactionOrchestrator()
    orchestrator.start_attempt()
    request = SimpleNamespace(messages=_tool_heavy_messages(n_tools=6, size=1000))

    handled = asyncio.run(
        _run_compaction_mode_dispatch(
            host,  # type: ignore[arg-type]
            context=_context(),
            request=request,
            session_memory=None,
            orchestrator=orchestrator,
            decision=_decision(),
            compaction_id="cmp_legacy",
            circuit_breaker_open_before=False,
        )
    )
    assert handled is True
    # Legacy partial path tags mode="partial", never "condenser_pipeline".
    payload = _outcome(host)
    assert payload["mode"] == "partial"
