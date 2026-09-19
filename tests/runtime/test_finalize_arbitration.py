"""AD-4: deterministic finalize-hook arbitration.

When several hooks request a revision, the winner is chosen by
``finalize_priority`` descending with registration order as the tie-break —
never by consultation/latency order. Suppressed requests are reported in
the ``finalize_revision_arbitrated`` event.
"""

from __future__ import annotations

import asyncio

from agent_driver.runtime.lifecycle_hooks import (
    BaseRunLifecycleHook,
    RevisionRequest,
    dispatch_finalize,
)


class _RequestingHook(BaseRunLifecycleHook):
    def __init__(
        self,
        name: str,
        feedback: str,
        *,
        priority: int = 0,
    ) -> None:
        self.name = name
        self.feedback = feedback
        self.finalize_priority = priority

    async def on_finalize(self, context, *, answer: str):  # noqa: ANN001, ANN202
        return RevisionRequest(feedback=self.feedback, gate_id=self.name)


def _run(hooks, emit=None):
    return asyncio.run(
        dispatch_finalize(hooks, context=None, answer="draft", emit=emit)
    )


def test_higher_priority_wins_regardless_of_registration_order() -> None:
    low = _RequestingHook("style_gate", "polish wording", priority=0)
    high = _RequestingHook("control_gate", "do not edit a proposed plan", priority=10)

    forward = _run([low, high])
    reverse = _run([high, low])

    assert forward.feedback == "do not edit a proposed plan"
    assert reverse.feedback == "do not edit a proposed plan"
    assert forward.gate_id == "control_gate"
    assert reverse.gate_id == "control_gate"


def test_equal_priority_keeps_registration_order() -> None:
    first = _RequestingHook("alpha_gate", "first")
    second = _RequestingHook("beta_gate", "second")

    winner = _run([first, second])
    assert winner.feedback == "first"
    winner = _run([second, first])
    assert winner.feedback == "second"


def test_arbitration_event_reports_suppressed_requests() -> None:
    low = _RequestingHook("style_gate", "polish wording", priority=0)
    high = _RequestingHook("control_gate", "keep the proposal", priority=5)
    events: list[tuple[str, dict]] = []

    def _emit(event_type: str, payload: dict) -> None:
        if event_type == "finalize_revision_arbitrated":
            events.append((event_type, payload))

    winner = _run([low, high], emit=_emit)

    assert winner.gate_id == "control_gate"
    assert len(events) == 1
    payload = events[0][1]
    assert payload["winner"] == "control_gate"
    assert payload["suppressed"] == [
        {"hook": "style_gate", "finalize_priority": 0, "gate_id": "style_gate"}
    ]


def test_negative_priority_loses_to_default() -> None:
    behind = _RequestingHook("behind", "behind", priority=-5)
    default = _RequestingHook("default", "default", priority=0)

    winner = _run([behind, default])
    assert winner.feedback == "default"


def test_single_request_and_no_request_are_unchanged() -> None:
    only = _RequestingHook("only", "only one")
    assert _run([only]).feedback == "only one"

    class _Silent(BaseRunLifecycleHook):
        name = "silent"

    assert _run([_Silent()]) is None


def test_priority_override_on_plain_hook_without_base() -> None:
    """A hook not subclassing the base still arbitrates via its attribute."""

    class _Plain:
        name = "plain_high"
        finalize_priority = 3

        async def on_finalize(self, context, *, answer: str):  # noqa: ANN001, ANN202
            return RevisionRequest(feedback="plain", gate_id="plain_high")

    base = _RequestingHook("base_default", "from base", priority=0)
    winner = _run([base, _Plain()])
    assert winner.feedback == "plain"
