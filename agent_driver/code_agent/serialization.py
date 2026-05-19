"""Safe executor-boundary serialization for CodeAgent."""

from __future__ import annotations

from typing import Any

from agent_driver.contracts.enums import SerializationMode
from agent_driver.contracts.serialization import ExecutorSerializationPolicy
from agent_driver.contracts.validation import ensure_json_serializable


def serialize_payload(
    payload: dict[str, Any], policy: ExecutorSerializationPolicy | None
) -> dict[str, Any]:
    """Serialize payload according to explicit safety policy."""
    if policy is None:
        return ensure_json_serializable(payload, field_name="code payload")
    if policy.mode == SerializationMode.JSON_SAFE:
        return ensure_json_serializable(payload, field_name="code payload")
    if (
        policy.mode == SerializationMode.UNSAFE_PICKLE_OPT_IN
        and policy.allow_unsafe_payloads
    ):
        return payload
    raise ValueError("unsafe serialization mode requires explicit opt-in")


def deserialize_payload(
    payload: dict[str, Any], policy: ExecutorSerializationPolicy | None
) -> dict[str, Any]:
    """Deserialize payload with the same fail-closed policy."""
    return serialize_payload(payload, policy)


__all__ = ["deserialize_payload", "serialize_payload"]
