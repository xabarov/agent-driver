"""Tests for scientific python sandbox imports."""

from __future__ import annotations

import pytest

from agent_driver.runtime.single_agent.config_sections import PythonToolSettings
from agent_driver.tools.builtin.python import python_tool_handler
from agent_driver.tools.builtin.python_imports import (
    effective_python_imports,
    parse_python_scientific_enabled,
    resolve_python_default_imports,
)
from agent_driver.code_agent.backends.local import LocalPythonBackend


@pytest.mark.asyncio
async def test_resolve_imports_includes_scientific_when_enabled() -> None:
    imports = resolve_python_default_imports(include_scientific=True)
    assert "numpy" in imports
    assert "scipy" in imports
    assert "pandas" in imports
    assert "math" in imports


def test_resolve_imports_excludes_scientific_when_disabled() -> None:
    imports = resolve_python_default_imports(include_scientific=False)
    assert "numpy" not in imports
    assert "scipy" not in imports
    assert "pandas" not in imports
    assert "math" in imports


def test_parse_python_scientific_enabled_cli_and_env() -> None:
    assert parse_python_scientific_enabled(no_python_scientific=True) is False
    assert parse_python_scientific_enabled(env_value="0") is False
    assert parse_python_scientific_enabled(env_value="false") is False
    assert parse_python_scientific_enabled(env_value="1") is True
    assert parse_python_scientific_enabled() is True


@pytest.mark.asyncio
async def test_handler_numpy_ok_when_scientific_on() -> None:
    settings = PythonToolSettings(
        enabled=True,
        include_scientific_stack=True,
        default_imports=resolve_python_default_imports(include_scientific=True),
    )
    backend = LocalPythonBackend()
    result = await python_tool_handler(
        args={"code": "import numpy as np\nprint(np.array([1, 2]).tolist())"},
        backend=backend,
        settings=settings,
    )
    assert result.get("error_kind") is None
    assert "1" in (result.get("stdout") or "")


@pytest.mark.asyncio
async def test_handler_scipy_gamma_cdf_ok_when_scientific_on() -> None:
    settings = PythonToolSettings(
        enabled=True,
        include_scientific_stack=True,
        default_imports=resolve_python_default_imports(include_scientific=True),
    )
    backend = LocalPythonBackend()
    result = await python_tool_handler(
        args={
            "code": (
                "import scipy.stats as stats\n"
                "m1, m2 = 3.2, 66.0\n"
                "var = m2 - m1 * m1\n"
                "theta = var / m1\n"
                "a = m1 / theta\n"
                "p = 1.0 - stats.gamma.cdf(5.0, a, scale=theta)\n"
                "print(p)"
            )
        },
        backend=backend,
        settings=settings,
    )
    assert result.get("error_kind") is None
    stdout = result.get("stdout") or ""
    value = float(stdout.strip().splitlines()[-1])
    assert 0.0 < value < 1.0


@pytest.mark.asyncio
async def test_handler_scipy_blocked_when_scientific_off() -> None:
    settings = PythonToolSettings(
        enabled=True,
        include_scientific_stack=False,
        default_imports=resolve_python_default_imports(include_scientific=False),
    )
    backend = LocalPythonBackend()
    result = await python_tool_handler(
        args={"code": "import scipy.stats"},
        backend=backend,
        settings=settings,
    )
    assert result.get("error_kind") == "policy"


def test_effective_python_imports_respects_include_scientific_stack() -> None:
    settings = PythonToolSettings(
        include_scientific_stack=False,
        default_imports=(),
    )
    imports = effective_python_imports(settings)
    assert "scipy" not in imports
