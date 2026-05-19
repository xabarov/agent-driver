"""CodeAgent policy and serialization tests."""

from __future__ import annotations

import pytest

from agent_driver.code_agent import (
    CodeAgentLimits,
    deserialize_payload,
    serialize_payload,
)
from agent_driver.code_agent.policy import validate_code_action
from agent_driver.contracts import ExecutorSerializationPolicy, SerializationMode


def test_policy_blocks_unauthorized_import_and_dunder() -> None:
    """Unauthorized imports and dunder access should fail closed."""
    violations = validate_code_action(
        code="import os\nx = value.__class__",
        limits=CodeAgentLimits(),
        authorized_imports={"math"},
    )
    codes = {item.code for item in violations}
    assert "forbidden_import" in codes
    assert "unauthorized_import" in codes
    assert "dunder_access" in codes


def test_policy_blocks_forbidden_function() -> None:
    """Forbidden builtins should be rejected."""
    violations = validate_code_action(
        code="exec('print(1)')",
        limits=CodeAgentLimits(),
        authorized_imports=set(),
    )
    assert any(item.code == "forbidden_function" for item in violations)


def test_json_safe_serialization_enforced() -> None:
    """JSON-safe mode should reject non-serializable payloads."""
    policy = ExecutorSerializationPolicy(mode=SerializationMode.JSON_SAFE)
    with pytest.raises(ValueError):
        serialize_payload({"x": {1, 2}}, policy)


def test_unsafe_serialization_requires_opt_in() -> None:
    """Unsafe mode without allow_unsafe_payloads must fail closed."""
    policy = ExecutorSerializationPolicy(mode=SerializationMode.UNSAFE_PICKLE_OPT_IN)
    with pytest.raises(ValueError):
        deserialize_payload({"x": object()}, policy)
