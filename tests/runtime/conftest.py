"""Shared helpers for runtime integration tests (governance / HITL harness)."""

from __future__ import annotations

import itertools
import os
from collections.abc import Iterator

import pytest

from agent_driver.contracts import (
    ApprovalMode,
    GuardrailDecision,
    SideEffectClass,
    ToolCall,
    ToolManifest,
    ToolPolicyInput,
    ToolPolicyMode,
    ToolRisk,
)
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmRequest
from agent_driver.tools import GuardrailPipeline, GuardrailResult


def danger_tool_manifest() -> ToolManifest:
    """High-risk manifest used across HITL policy tests."""
    return ToolManifest(
        name="danger",
        description="Danger",
        risk=ToolRisk.HIGH,
        side_effect=SideEffectClass.EXTERNAL_ACTION,
        approval_mode=ApprovalMode.ALWAYS,
    )


def planned_danger_tool_policy() -> ToolPolicyInput:
    """Tool policy that plans a dangerous call requiring approval."""
    return ToolPolicyInput(
        mode=ToolPolicyMode.ALLOW_TOOLS,
        approval_required_for_risk=ToolRisk.HIGH,
        metadata={
            "planned_tool_calls": [{"tool_name": "danger", "args": {"target": "x"}}]
        },
    )


def llm_request_with_planned_calls(planned: list[ToolCall]) -> LlmRequest:
    """Build LLM request carrying JSON-safe planned tool calls."""
    return LlmRequest(
        messages=[ChatMessage(role="user", content="hello")],
        metadata={
            "planned_tool_calls": [call.model_dump(mode="json") for call in planned]
        },
    )


_PG_SCHEMA_COUNTER = itertools.count()


def _require_postgres() -> bool:
    """True when the mandatory postgres CI job demands real-Postgres tests run."""
    return os.environ.get("AD_REQUIRE_POSTGRES", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _fail_or_skip(message: str) -> None:
    """Fail (not skip) when AD_REQUIRE_POSTGRES=1; otherwise skip locally."""
    if _require_postgres():
        pytest.fail(message, pytrace=False)
    pytest.skip(message)


@pytest.fixture(scope="session")
def postgres_dsn() -> str:
    """Return a live Postgres DSN or fail/skip per AD_REQUIRE_POSTGRES.

    The mandatory postgres CI job sets ``AD_REQUIRE_POSTGRES=1`` and a
    ``AD_POSTGRES_TEST_DSN``; a missing DSN, a missing psycopg dependency, or an
    unreachable server there is a hard **failure**, never a green skip. Locally
    (env unset) these degrade to skips so the default sweep stays fast.
    """
    dsn = os.environ.get("AD_POSTGRES_TEST_DSN", "").strip()
    if not dsn:
        _fail_or_skip(
            "AD_POSTGRES_TEST_DSN is unset — the mandatory postgres job must "
            "provide a DSN (real-Postgres control-plane matrix)."
        )
    try:
        import psycopg
    except ImportError:
        _fail_or_skip(
            "psycopg is not installed — install agent-driver[postgres] for the "
            "postgres job."
        )
    try:
        psycopg.connect(dsn, connect_timeout=5).close()
    except Exception as exc:  # noqa: BLE001 - any connect failure is fatal in CI
        _fail_or_skip(f"cannot connect to postgres DSN: {exc}")
    return dsn


@pytest.fixture
def pg_control_config(postgres_dsn: str) -> "Iterator[object]":
    """Yield a PostgresControlStoreConfig bound to a fresh, isolated schema.

    Each test gets its own throwaway schema (dropped on teardown) so the
    real-Postgres matrix never collides across tests or reruns.
    """
    import psycopg

    from agent_driver.runtime.control import PostgresControlStoreConfig

    schema = f"ad_ctl_test_{next(_PG_SCHEMA_COUNTER)}"
    with psycopg.connect(postgres_dsn, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        conn.execute(f"CREATE SCHEMA {schema}")
    try:
        yield PostgresControlStoreConfig(dsn=postgres_dsn, schema=schema)
    finally:
        with psycopg.connect(postgres_dsn, autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


class BlockingToolArgsGuardrails(GuardrailPipeline):
    """Blocks execution when args contain ``blocked: True``."""

    async def on_tool_args(self, payload: dict[str, object]) -> GuardrailResult:
        if payload.get("args", {}).get("blocked"):
            return GuardrailResult(
                decision=GuardrailDecision.BLOCK,
                reason="args blocked by guardrail",
            )
        return await super().on_tool_args(payload)


class BlockingToolInputGuardrails(GuardrailPipeline):
    """Blocks lookup tools at the input validation stage."""

    async def on_input(self, payload: dict[str, object]) -> GuardrailResult:
        if payload.get("tool_name") == "lookup":
            return GuardrailResult(
                decision=GuardrailDecision.BLOCK,
                reason="input blocked by guardrail",
            )
        return await super().on_input(payload)


class SanitizeToolResultGuardrails(GuardrailPipeline):
    """Marks tool results for sanitization."""

    async def on_tool_result(self, payload: dict[str, object]) -> GuardrailResult:
        _ = payload
        return GuardrailResult(
            decision=GuardrailDecision.SANITIZE,
            reason="sanitize marker",
        )
