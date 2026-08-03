"""Explicit persisted-state serialization for rolling rollback windows."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from agent_driver.runtime.state import RuntimeState

ROLLBACK_TARGET_0_2_RC5 = "0.2.0rc5"


@dataclass(frozen=True, slots=True)
class RuntimeStateCompatibilityResult:
    """Compatibility payload plus a raw-free transformation audit."""

    target: str
    payload: dict[str, Any]
    removed_paths: tuple[str, ...]
    transformed_paths: tuple[str, ...]


def _mapping_payload(state: RuntimeState | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(state, RuntimeState):
        return state.model_dump(mode="json")
    return deepcopy(dict(state))


def _remove(
    parent: dict[str, Any],
    key: str,
    path: str,
    removed: list[str],
) -> None:
    if key in parent:
        parent.pop(key, None)
        removed.append(path)


def serialize_runtime_state_for_compatibility(
    state: RuntimeState | Mapping[str, Any],
    *,
    target: str,
) -> RuntimeStateCompatibilityResult:
    """Serialize state for a declared older reader without resetting storage.

    The ``0.2.0rc5`` profile removes only additive checkpoint/approval fields
    unknown to that strict reader. A typed context budget is down-converted to
    the legacy ``app_metadata.context_budget`` mapping before its new field is
    removed. The audit records paths only; it never copies message, evidence,
    tool payload, or reasoning content.
    """
    if target != ROLLBACK_TARGET_0_2_RC5:
        raise ValueError(f"unsupported runtime-state compatibility target: {target}")

    payload = _mapping_payload(state)
    removed: list[str] = []
    transformed: list[str] = []

    checkpoint = payload.get("checkpoint")
    if isinstance(checkpoint, dict):
        _remove(checkpoint, "revision", "checkpoint.revision", removed)

    latest_output = payload.get("latest_output")
    if isinstance(latest_output, dict):
        output_checkpoint = latest_output.get("checkpoint")
        if isinstance(output_checkpoint, dict):
            _remove(
                output_checkpoint,
                "revision",
                "latest_output.checkpoint.revision",
                removed,
            )

    run_input = payload.get("run_input")
    if isinstance(run_input, dict):
        typed_budget = run_input.get("context_budget")
        if isinstance(typed_budget, dict):
            app_metadata = run_input.get("app_metadata")
            if not isinstance(app_metadata, dict):
                app_metadata = {}
                run_input["app_metadata"] = app_metadata
            app_metadata["context_budget"] = {
                "input_tokens": typed_budget.get("input_tokens"),
                "output_tokens": typed_budget.get("output_tokens", 0),
            }
            transformed.append(
                "run_input.context_budget->run_input.app_metadata.context_budget"
            )
        _remove(run_input, "context_budget", "run_input.context_budget", removed)

        resume = run_input.get("resume")
        if isinstance(resume, dict):
            for field in (
                "idempotency_key",
                "expected_checkpoint_id",
                "expected_revision",
            ):
                _remove(resume, field, f"run_input.resume.{field}", removed)

    return RuntimeStateCompatibilityResult(
        target=target,
        payload=payload,
        removed_paths=tuple(sorted(removed)),
        transformed_paths=tuple(sorted(transformed)),
    )


__all__ = [
    "ROLLBACK_TARGET_0_2_RC5",
    "RuntimeStateCompatibilityResult",
    "serialize_runtime_state_for_compatibility",
]
