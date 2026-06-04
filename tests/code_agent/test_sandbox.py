"""Tests for the hardened object-returning Python sandbox."""

from __future__ import annotations

import pytest

from agent_driver.code_agent import (
    run_sandboxed,
    SandboxLimits,
    SandboxPolicyError,
    SandboxResult,
    SandboxTimeoutError,
)


def test_returns_last_expression_object() -> None:
    """The value of a trailing bare expression is returned as a real object."""
    result = run_sandboxed("a = [1, 2, 3]\nsum(a)")
    assert isinstance(result, SandboxResult)
    assert result.result == 6
    assert result.result_repr_only is False


def test_returns_result_variable_object() -> None:
    """A ``result`` variable is preferred and returned as a live object."""
    out = run_sandboxed("result = {'k': [1, 2]}\nprint('hi')")
    assert out.result == {"k": [1, 2]}
    assert "hi" in out.stdout


def test_result_var_lookup_order() -> None:
    """First present name in result_vars wins over later ones."""
    out = run_sandboxed("output = 7\ndata = 9", result_vars=("result", "output", "data"))
    assert out.result == 7


def test_initial_state_injection() -> None:
    """Pre-injected state is visible to the executed code."""
    out = run_sandboxed("result = base + 5", initial_state={"base": 10})
    assert out.result == 15


def test_authorized_import_allowed() -> None:
    """An allowlisted module imports successfully."""
    out = run_sandboxed("import json\nresult = json.dumps({'x': 1})", authorized_imports=["json"])
    assert out.result == '{"x": 1}'


def test_unauthorized_import_blocked() -> None:
    """Importing a non-allowlisted module raises a policy error."""
    with pytest.raises(SandboxPolicyError):
        run_sandboxed("import os\nresult = os.getcwd()", authorized_imports=["json"])


def test_open_builtin_unavailable() -> None:
    """``open`` is withheld from user code (FS posture parity)."""
    with pytest.raises(Exception):
        run_sandboxed("result = open('/etc/passwd').read()")


def test_wall_clock_timeout() -> None:
    """A runaway loop is killed at the wall-clock limit."""
    with pytest.raises(SandboxTimeoutError):
        run_sandboxed(
            "while True:\n    pass",
            limits=SandboxLimits(max_exec_seconds=1.0, max_cpu_seconds=2),
        )


def test_exception_surfaced() -> None:
    """An in-sandbox exception is surfaced as a SandboxError message."""
    from agent_driver.code_agent import SandboxError

    with pytest.raises(SandboxError):
        run_sandboxed("result = 1 / 0")


def test_non_picklable_result_falls_back_to_repr() -> None:
    """A non-picklable result is returned as its repr with a flag set."""
    out = run_sandboxed("result = lambda x: x")
    assert out.result_repr_only is True
    assert isinstance(out.result, str)


def test_stdout_truncation() -> None:
    """Oversized stdout is truncated and flagged."""
    out = run_sandboxed(
        "print('x' * 50)\nresult = 1",
        limits=SandboxLimits(max_output_chars=10),
    )
    assert out.truncated_output is True
    assert len(out.stdout) <= 13  # 10 + "..."
