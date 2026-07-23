"""Epic 037: observer/middleware classification, versions, correlation."""

from __future__ import annotations

import inspect

import pytest

from agent_driver.contracts.observability import (
    CLASSIFIED_HOOK_METHODS,
    MIDDLEWARE_HOOK_METHODS,
    MIDDLEWARE_SCHEMA_VERSION,
    OBSERVER_HOOK_METHODS,
    OBSERVER_SCHEMA_VERSION,
    correlation_ids,
    describe_observability_contract,
    deterministic_trace_id,
    hook_method_role,
)
from agent_driver.runtime.lifecycle_hooks import BaseRunLifecycleHook


def _protocol_hook_methods() -> set[str]:
    """Every async lifecycle-hook method the base declares (the Protocol surface)."""
    return {
        name
        for name, member in inspect.getmembers(
            BaseRunLifecycleHook, predicate=inspect.isfunction
        )
        if not name.startswith("_") and inspect.iscoroutinefunction(member)
    }


def test_every_hook_method_is_classified_exactly_once() -> None:
    """Lock: adding a hook method forces an observer-vs-middleware decision.

    Both directions — no unclassified Protocol method, and no phantom entry in
    the sets that isn't a real method.
    """
    protocol = _protocol_hook_methods()
    assert protocol == CLASSIFIED_HOOK_METHODS, (
        f"unclassified={protocol - CLASSIFIED_HOOK_METHODS}, "
        f"phantom={CLASSIFIED_HOOK_METHODS - protocol}"
    )
    # Disjoint: a method is observer XOR middleware, never both.
    assert OBSERVER_HOOK_METHODS.isdisjoint(MIDDLEWARE_HOOK_METHODS)


def test_hook_method_role() -> None:
    assert hook_method_role("on_run_completed") == "observer"
    assert hook_method_role("before_llm_request") == "middleware"
    assert hook_method_role("nonexistent") is None


def test_version_strings_are_stable() -> None:
    assert OBSERVER_SCHEMA_VERSION == "agent_driver.observer.v1"
    assert MIDDLEWARE_SCHEMA_VERSION == "agent_driver.middleware.v1"


def test_describe_contract_shape() -> None:
    desc = describe_observability_contract()
    assert desc["observer_schema_version"] == OBSERVER_SCHEMA_VERSION
    assert desc["middleware_schema_version"] == MIDDLEWARE_SCHEMA_VERSION
    assert "on_run_completed" in desc["observer_methods"]
    assert "on_finalize" in desc["middleware_methods"]


def test_deterministic_trace_id_matches_trace_builder() -> None:
    """The emit-path seed must be byte-identical to the trace export's."""
    from agent_driver.observability.trace_builder import _trace_id_for_output

    class _Out:
        run_id = "run_abc"
        attempt_id = "attempt_9"

    assert deterministic_trace_id("run_abc", "attempt_9") == _trace_id_for_output(
        _Out()
    )


def test_correlation_ids_includes_thread_only_when_present() -> None:
    ids = correlation_ids("r", "a", thread_id="t")
    assert ids == {
        "run_id": "r",
        "attempt_id": "a",
        "trace_id": deterministic_trace_id("r", "a"),
        "thread_id": "t",
    }
    assert "thread_id" not in correlation_ids("r", "a")


@pytest.mark.asyncio
async def test_emitted_events_carry_deterministic_trace_id() -> None:
    """Phase B: every emitted event correlates to its span by construction."""
    from agent_driver.contracts import AgentRunInput
    from agent_driver.llm.providers_impl.fake import FakeProvider
    from agent_driver.sdk import ToolSet, create_agent

    out = await create_agent(
        provider=FakeProvider(response_text="готово"), tools=ToolSet.only()
    ).run(
        AgentRunInput(
            input="q",
            run_id="run_corr",
            thread_id="t",
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    expected = deterministic_trace_id(out.run_id, out.attempt_id)
    assert out.events, "run should emit events"
    assert all(e.trace_id == expected for e in out.events), [
        (e.type, e.trace_id) for e in out.events
    ]
