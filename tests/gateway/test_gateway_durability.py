"""U6 (epic 054) — Gateway is explicitly non-durable; direct path is durable.

Option 2: the Gateway declares that parked-run recovery is process-local, a
readiness check fails fast when durable recovery is required, and the direct
embedding path exposes the durable primitives a host needs instead.
"""

from __future__ import annotations

import pytest

from agent_driver.gateway import AgentGateway, GatewayError
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import ToolSet, create_agent


def _gateway() -> AgentGateway:
    agent = create_agent(
        provider=FakeProvider(response_text="done"), tools=ToolSet.only()
    )
    return AgentGateway(agent)


def test_gateway_declares_non_durable_parked_runs() -> None:
    assert AgentGateway.durable_parked_runs is False
    assert _gateway().durable_parked_runs is False


def test_require_durable_recovery_fails_fast() -> None:
    with pytest.raises(GatewayError, match="cannot .*recover|durable recovery"):
        _gateway().require_durable_recovery()


def test_direct_embedding_path_exposes_durable_primitives() -> None:
    """The recovery a host needs lives on the direct path, not the Gateway."""
    import agent_driver.runtime as rt

    for name in (
        "SqliteRuntimeStore",
        "PostgresRuntimeStore",
        "InMemoryCheckpointStore",
        "RunAbortHandle",
    ):
        assert hasattr(rt, name), name
    # Durable command/control store family (steering / cross-process interrupt).
    from agent_driver.runtime.control import SqliteCommandQueueStore

    assert SqliteCommandQueueStore is not None
