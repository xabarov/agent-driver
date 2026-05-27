"""Tests for python builtin tool handler.

Every test below instantiates ``LocalPythonBackend`` (real subprocess
spawn + interpreter warm-up). Tagged ``slow`` at file scope; included
under ``pytest -m slow`` or ``-m 'slow or not slow'``.
"""

from __future__ import annotations

import pytest

from agent_driver.code_agent.backends.local import LocalPythonBackend
from agent_driver.code_agent.contracts import CodeAgentLimits
from agent_driver.runtime.single_agent.config_sections import PythonToolSettings
from agent_driver.tools.builtin.python import python_tool_handler

pytestmark = pytest.mark.slow


@pytest.mark.asyncio
async def test_python_handler_executes_code_with_persistent_session() -> None:
    backend = LocalPythonBackend(session_idle_seconds=30.0)
    settings = PythonToolSettings(
        enabled=True,
        backend="local",
        limits=CodeAgentLimits(max_exec_ms=300, max_output_chars=200),
    )
    first = await python_tool_handler(
        args={"code": "x = 4 * 10", "session_id": "s1"},
        backend=backend,
        settings=settings,
    )
    second = await python_tool_handler(
        args={"code": "print(x)\nx", "session_id": "s1"},
        backend=backend,
        settings=settings,
    )
    await backend.aclose()
    assert "python ok:" in str(first["summary"])
    assert second["stdout"].strip() == "40"
    assert second["result_repr"] == "40"


@pytest.mark.asyncio
async def test_python_handler_returns_error_summary_for_policy_violation() -> None:
    backend = LocalPythonBackend(session_idle_seconds=30.0)
    settings = PythonToolSettings(enabled=True, backend="local")
    result = await python_tool_handler(
        args={"code": "import os\nprint('x')", "session_id": "s2"},
        backend=backend,
        settings=settings,
    )
    await backend.aclose()
    assert str(result["summary"]).startswith("python policy:")
    assert result.get("error_kind") == "policy"
    assert result["policy_reasons"]


@pytest.mark.asyncio
async def test_python_handler_supports_safe_imports() -> None:
    backend = LocalPythonBackend(session_idle_seconds=30.0)
    settings = PythonToolSettings(enabled=True, backend="local")
    result = await python_tool_handler(
        args={"code": "import math\nmath.log(345)", "session_id": "s_import"},
        backend=backend,
        settings=settings,
    )
    await backend.aclose()
    assert str(result["summary"]).startswith("python ok:")
    assert result["result_repr"] is not None
    assert "result=" in str(result["summary"])


@pytest.mark.asyncio
async def test_python_handler_scipy_policy_summary_mentions_sandbox_block() -> None:
    backend = LocalPythonBackend(session_idle_seconds=30.0)
    settings = PythonToolSettings(
        enabled=True,
        backend="local",
        include_scientific_stack=False,
        default_imports=(),
    )
    result = await python_tool_handler(
        args={"code": "import scipy\nimport numpy", "session_id": "s_scipy"},
        backend=backend,
        settings=settings,
    )
    await backend.aclose()
    summary = str(result["summary"])
    assert result.get("error_kind") == "policy"
    assert "blocked by sandbox" in summary
    assert "not missing" in summary.lower()
    assert "scipy" in summary or "numpy" in summary
    assert "not installed" not in summary.lower()


@pytest.mark.asyncio
async def test_python_handler_error_contains_allowlist_remediation() -> None:
    backend = LocalPythonBackend(session_idle_seconds=30.0)
    settings = PythonToolSettings(enabled=True, backend="local")
    result = await python_tool_handler(
        args={"code": "import os\nprint('x')", "session_id": "s_policy"},
        backend=backend,
        settings=settings,
    )
    await backend.aclose()
    assert str(result["summary"]).startswith("python policy:")
    assert isinstance(result.get("allowed_imports"), list)
    assert "remediation" in result


@pytest.mark.asyncio
async def test_python_handler_error_uses_effective_overlay_allowlist() -> None:
    backend = LocalPythonBackend(session_idle_seconds=30.0)
    settings = PythonToolSettings(enabled=True, backend="local", allow_overlay=True)
    result = await python_tool_handler(
        args={
            "code": "import os\nprint('x')",
            "session_id": "s_overlay",
            "authorized_imports": ["fractions"],
        },
        backend=backend,
        settings=settings,
    )
    await backend.aclose()
    allowed = result.get("allowed_imports")
    assert isinstance(allowed, list)
    assert "fractions" in allowed


@pytest.mark.asyncio
async def test_python_handler_runtime_error_uses_runtime_error_kind() -> None:
    backend = LocalPythonBackend(session_idle_seconds=30.0)
    settings = PythonToolSettings(enabled=True, backend="local")
    result = await python_tool_handler(
        args={"code": "print(missing_value)", "session_id": "s_runtime"},
        backend=backend,
        settings=settings,
    )
    await backend.aclose()
    assert str(result["summary"]).startswith("python error:")
    assert result.get("error_kind") == "runtime"
    assert "allowed_imports" not in result
    assert "remediation" in result


@pytest.mark.asyncio
async def test_python_handler_returns_tip_when_no_visible_output() -> None:
    backend = LocalPythonBackend(session_idle_seconds=30.0)
    settings = PythonToolSettings(enabled=True, backend="local")
    result = await python_tool_handler(
        args={"code": "x = 1", "session_id": "s_tip"},
        backend=backend,
        settings=settings,
    )
    await backend.aclose()
    assert result.get("tip")


@pytest.mark.asyncio
async def test_python_handler_timeout_resets_session() -> None:
    backend = LocalPythonBackend(session_idle_seconds=30.0)
    settings = PythonToolSettings(
        enabled=True,
        backend="local",
        limits=CodeAgentLimits(max_exec_ms=60, max_output_chars=200),
    )
    timeout_result = await python_tool_handler(
        args={"code": "while True:\n    pass", "session_id": "s3"},
        backend=backend,
        settings=settings,
    )
    next_result = await python_tool_handler(
        args={"code": "print('ok')", "session_id": "s3"},
        backend=backend,
        settings=settings,
    )
    await backend.aclose()
    assert str(timeout_result["summary"]).startswith("python error:")
    assert timeout_result.get("error_kind") == "runtime"
    assert next_result["stdout"].strip() == "ok"
