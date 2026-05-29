"""Phase 11 H17 — tests for the interrupt_behavior contract field.

Pins the default-derivation rules:

* IRREVERSIBLE_WRITE / EXTERNAL_ACTION → "block"
* NONE / READ_ONLY / REVERSIBLE_WRITE → "cancel"
* Explicit ``interrupt_behavior=...`` overrides derivation either way.

Runtime wiring (actually pausing / cancelling on a new user message) is
a separate runtime concern; this contract field is the prerequisite
that lets the runtime decide.
"""

from __future__ import annotations

import pytest

from agent_driver.contracts.enums import SideEffectClass
from agent_driver.contracts.tools import ToolManifest


def _manifest(**overrides):
    base = dict(
        name="t",
        description="t",
    )
    base.update(overrides)
    return ToolManifest(**base)


@pytest.mark.parametrize(
    "side_effect, expected",
    [
        (SideEffectClass.NONE, "cancel"),
        (SideEffectClass.READ_ONLY, "cancel"),
        (SideEffectClass.REVERSIBLE_WRITE, "cancel"),
        (SideEffectClass.IRREVERSIBLE_WRITE, "block"),
        (SideEffectClass.EXTERNAL_ACTION, "block"),
    ],
)
def test_default_derivation_from_side_effect(side_effect, expected):
    """Default behaviour reflects safety: writes that can't be undone
    must block; safe / reversible operations may be cancelled."""
    m = _manifest(side_effect=side_effect)
    assert m.resolved_interrupt_behavior() == expected


def test_explicit_block_overrides_cancel_default():
    """Operator marks a read-only call as block (e.g. very long
    download that should still finish to populate cache)."""
    m = _manifest(
        side_effect=SideEffectClass.READ_ONLY,
        interrupt_behavior="block",
    )
    assert m.resolved_interrupt_behavior() == "block"


def test_explicit_cancel_overrides_block_default():
    """Operator marks an external action as cancel (e.g. an idempotent
    POST that's safe to abort because the endpoint deduplicates)."""
    m = _manifest(
        side_effect=SideEffectClass.EXTERNAL_ACTION,
        interrupt_behavior="cancel",
    )
    assert m.resolved_interrupt_behavior() == "cancel"


def test_interrupt_behavior_serialization_round_trip():
    """Field round-trips through model_dump / model_validate."""
    m = _manifest(interrupt_behavior="block")
    raw = m.model_dump()
    assert raw["interrupt_behavior"] == "block"
    m2 = ToolManifest.model_validate(raw)
    assert m2.interrupt_behavior == "block"
    assert m2.resolved_interrupt_behavior() == "block"


def test_invalid_interrupt_behavior_value_rejected():
    """Literal validation: only ``cancel``/``block`` are accepted."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _manifest(interrupt_behavior="ignore")  # type: ignore[arg-type]
