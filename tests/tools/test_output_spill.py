"""Phase 12 H18 — tests for tool output disk-spill via artifact store.

Pins:
* manifest ``max_result_size_chars=None`` (default) → no spill;
* small handler output → no spill regardless of setting;
* large output + manifest opt-in + artifact store wired → spilled to
  store; envelope carries preview + artifact ref;
* large output + manifest opt-in but NO store wired → legacy
  truncation (no spill, no crash);
* large output + manifest NOT opted in → legacy truncation
  (backwards compat);
* store failure → falls back to legacy truncation, doesn't crash.
"""

from __future__ import annotations

import json

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
from agent_driver.contracts.context.artifacts import (
    ArtifactPreview,
    ContextArtifactRef,
    StoredArtifact,
)
from agent_driver.context.artifacts.in_memory import InMemoryArtifactStore
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.tools import GovernedToolExecutor, ToolRegistry
from agent_driver.tools.context import workspace_cwd_scope
from agent_driver.tools.executor.spill import (
    PREVIEW_MAX_CHARS,
    should_spill_payload,
    spill_payload_to_artifact,
)
from tests.runtime.conftest import llm_request_with_planned_calls


def _build_run_input(run_id: str) -> AgentRunInput:
    return AgentRunInput(
        input="hello",
        run_id=run_id,
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
    )


def _make_large_tool(
    registry: ToolRegistry, *, size_chars: int, max_result: int | None
):
    """Register a tool returning a JSON payload of approximately `size_chars`."""

    async def _handler(_args):
        # Build a wide list payload that JSON-encodes near the target.
        items = [f"item-{i}-padding-text" for i in range(size_chars // 30)]
        return {"summary": "large output produced", "items": items}

    registry.register(
        ToolManifest(
            name="bulk_tool",
            description="emits large data",
            risk=ToolRisk.LOW,
            side_effect=SideEffectClass.READ_ONLY,
            approval_mode=ApprovalMode.NEVER,
            idempotent=True,
            output_char_budget=5000,
            max_result_size_chars=max_result,
        ),
        _handler,
    )


# -- contract helpers (pure) -----------------------------------------------


def test_should_spill_false_when_no_store():
    assert (
        should_spill_payload(
            payload={"items": ["a"] * 100000},
            max_result_size_chars=100,
            store=None,
        )
        is False
    )


def test_should_spill_false_when_no_threshold():
    store = InMemoryArtifactStore()
    assert (
        should_spill_payload(
            payload={"items": ["a"] * 100000},
            max_result_size_chars=None,
            store=store,
        )
        is False
    )


def test_should_spill_false_when_under_threshold():
    store = InMemoryArtifactStore()
    assert (
        should_spill_payload(
            payload={"summary": "ok"},
            max_result_size_chars=10000,
            store=store,
        )
        is False
    )


def test_should_spill_true_when_over_threshold():
    store = InMemoryArtifactStore()
    payload = {"items": list(range(2000))}  # JSON >> 100 bytes
    assert (
        should_spill_payload(
            payload=payload,
            max_result_size_chars=100,
            store=store,
        )
        is True
    )


def test_spill_returns_replacement_with_preview_and_ref():
    store = InMemoryArtifactStore()
    payload = {"items": list(range(2000)), "summary": "lots"}
    result = spill_payload_to_artifact(
        payload=payload,
        store=store,
        tool_name="bulk_tool",
        run_id="r1",
        tool_call_id="att_1",
    )
    assert result is not None
    replacement, ref = result
    assert replacement["persisted"] is True
    assert replacement["truncated"] is False
    assert "preview" in replacement
    assert len(replacement["preview"]) <= PREVIEW_MAX_CHARS + 1  # +1 for ellipsis char
    assert replacement["persisted_artifact"]["artifact_id"] == ref.artifact_id
    assert ref.kind.value == "tool_result"
    # Full payload retrievable via store.get().
    stored = store.get(ref.artifact_id)
    assert stored is not None
    decoded = json.loads(stored.content)
    assert decoded == payload


def test_spill_mirrors_payload_to_workspace_tool_results(tmp_path):
    store = InMemoryArtifactStore()
    payload = {"items": list(range(2000)), "summary": "lots"}
    with workspace_cwd_scope(tmp_path):
        result = spill_payload_to_artifact(
            payload=payload,
            store=store,
            tool_name="bulk_tool",
            run_id="r1",
            tool_call_id="call_bulk",
        )

    assert result is not None
    replacement, _ = result
    assert replacement["workspace_artifact_path"] == "tool-results/call_bulk.json"
    assert (
        replacement["persisted_artifact"]["workspace_path"]
        == "tool-results/call_bulk.json"
    )
    mirrored = tmp_path / "tool-results" / "call_bulk.json"
    assert json.loads(mirrored.read_text(encoding="utf-8")) == payload


def test_spill_failure_returns_none_caller_falls_back():
    """Mock store that raises → spill returns None; caller falls back."""

    class BadStore:
        def put(self, _artifact):
            raise RuntimeError("disk full")

    payload = {"items": list(range(2000))}
    result = spill_payload_to_artifact(
        payload=payload,
        store=BadStore(),
        tool_name="bulk_tool",
        run_id="r1",
    )
    assert result is None


# -- end-to-end through GovernedToolExecutor -------------------------------


@pytest.mark.asyncio
async def test_executor_spills_large_output_when_store_wired(tmp_path):
    """Large output + manifest opted in + store on executor → spilled."""
    registry = ToolRegistry()
    _make_large_tool(registry, size_chars=80000, max_result=10000)
    store = InMemoryArtifactStore()
    executor = GovernedToolExecutor(registry=registry, artifact_store=store)

    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[
                ToolCall(
                    tool_name="bulk_tool",
                    tool_call_id="call_bulk",
                    args={},
                )
            ]
        )
    )
    with workspace_cwd_scope(tmp_path):
        result = await executor.execute(_build_run_input("r_spill"), response)

    envelope = result.envelopes[0]
    raw = envelope.structured_output
    assert raw.get("persisted") is True
    assert "persisted_artifact" in raw
    artifact_id = raw["persisted_artifact"]["artifact_id"]
    assert raw["workspace_artifact_path"] == "tool-results/call_bulk.json"
    assert raw["persisted_artifact"]["workspace_path"] == "tool-results/call_bulk.json"
    assert (tmp_path / "tool-results" / "call_bulk.json").is_file()
    # Store has the full payload available.
    stored = store.get(artifact_id)
    assert stored is not None
    # Stored content well exceeds the threshold (10_000); demonstrates
    # that the full handler output landed in the store, not just preview.
    assert len(stored.content) >= 50000


@pytest.mark.asyncio
async def test_executor_no_spill_without_store():
    """Manifest opted in but executor has no store → legacy truncation."""
    registry = ToolRegistry()
    _make_large_tool(registry, size_chars=80000, max_result=10000)
    executor = GovernedToolExecutor(registry=registry)  # no artifact_store

    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="bulk_tool", args={})]
        )
    )
    result = await executor.execute(_build_run_input("r_no_store"), response)
    raw = result.envelopes[0].structured_output
    # No persistence marker.
    assert "persisted" not in raw or raw.get("persisted") is not True


@pytest.mark.asyncio
async def test_executor_no_spill_when_manifest_not_opted_in():
    """Default ToolManifest with max_result_size_chars=None → no spill."""
    registry = ToolRegistry()
    _make_large_tool(registry, size_chars=80000, max_result=None)
    store = InMemoryArtifactStore()
    executor = GovernedToolExecutor(registry=registry, artifact_store=store)

    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="bulk_tool", args={})]
        )
    )
    await executor.execute(_build_run_input("r_no_optin"), response)
    # Store should be empty — no spill happened.
    by_kind = store.list_for_kind("tool_result")
    assert by_kind == []


@pytest.mark.asyncio
async def test_executor_no_spill_when_output_fits_budget():
    """Small output stays in-context even when spill is enabled."""
    registry = ToolRegistry()

    async def _small(_args):
        return {"summary": "tiny", "items": ["a", "b"]}

    registry.register(
        ToolManifest(
            name="small_tool",
            description="small",
            risk=ToolRisk.LOW,
            side_effect=SideEffectClass.READ_ONLY,
            approval_mode=ApprovalMode.NEVER,
            idempotent=True,
            output_char_budget=2000,
            max_result_size_chars=10000,
        ),
        _small,
    )
    store = InMemoryArtifactStore()
    executor = GovernedToolExecutor(registry=registry, artifact_store=store)

    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="small_tool", args={})]
        )
    )
    result = await executor.execute(_build_run_input("r_small"), response)
    raw = result.envelopes[0].structured_output
    assert raw.get("persisted") is not True
    # Store should remain empty.
    assert store.list_for_kind("tool_result") == []
