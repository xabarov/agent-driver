"""Tests for local python backend abstraction."""

from __future__ import annotations

import pytest

from agent_driver.code_agent.backends.local import LocalPythonBackend
from agent_driver.code_agent.contracts import CodeAgentLimits


@pytest.mark.asyncio
async def test_local_backend_persists_values_per_session() -> None:
    backend = LocalPythonBackend(session_idle_seconds=30.0)
    limits = CodeAgentLimits(max_exec_ms=300)
    await backend.execute(
        code="value = 7",
        session_id="alpha",
        authorized_imports=set(),
        limits=limits,
        serialization_policy=None,
    )
    result = await backend.execute(
        code="value",
        session_id="alpha",
        authorized_imports=set(),
        limits=limits,
        serialization_policy=None,
    )
    await backend.aclose()
    assert result.metadata.get("result_repr") == "7"


@pytest.mark.asyncio
async def test_local_backend_close_session_drops_state() -> None:
    backend = LocalPythonBackend(session_idle_seconds=30.0)
    limits = CodeAgentLimits(max_exec_ms=300)
    await backend.execute(
        code="value = 9",
        session_id="beta",
        authorized_imports=set(),
        limits=limits,
        serialization_policy=None,
    )
    await backend.close_session("beta")
    with pytest.raises(Exception):
        await backend.execute(
            code="value",
            session_id="beta",
            authorized_imports=set(),
            limits=limits,
            serialization_policy=None,
        )
    await backend.aclose()


@pytest.mark.asyncio
async def test_local_backend_imports_work_inside_defined_functions() -> None:
    backend = LocalPythonBackend(session_idle_seconds=30.0)
    limits = CodeAgentLimits(max_exec_ms=300)
    result = await backend.execute(
        code=(
            "import math\n"
            "def calc():\n"
            "    return math.log(2)\n"
            "print(calc())"
        ),
        session_id="gamma",
        authorized_imports={"math"},
        limits=limits,
        serialization_policy=None,
    )
    await backend.aclose()
    stdout = next(item.text_preview for item in result.observations if item.source == "stdout")
    assert "0.693147" in stdout
