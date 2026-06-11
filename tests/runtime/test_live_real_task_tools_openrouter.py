"""Opt-in live real-task validation: the SDK agent on genuine tool tasks.

These exercise the full multi-turn tool loop (filesystem + sandboxed Python)
against a real OpenRouter-compatible open-weight model in a throwaway temp
workspace. Unlike the offline FakeProvider suites, they surface defects that
only appear with a real model driving real tools: tool-arg shaping, multi-step
recovery, and end-to-end correctness of executed Python.

Run with::

    AGENT_DRIVER_RUN_LIVE_TESTS=1 .venv/bin/python -m pytest -m live \
        tests/runtime/test_live_real_task_tools_openrouter.py

Requires AGENT_DRIVER_BASE_URL / AGENT_DRIVER_MODEL / AGENT_DRIVER_API_KEY in
the environment (or a local .env). Each test costs a few hundred tokens.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from agent_driver.contracts import AgentRunInput
from agent_driver.llm import resolve_provider
from agent_driver.llm.provider_descriptors import ProviderSpec
from agent_driver.runtime import RunnerConfig
from agent_driver.runtime.single_agent.lifecycle.config_sections import (
    PythonToolSettings,
)
from agent_driver.sdk import ToolSet, create_agent
from tests.live_env import load_local_dotenv_for_live_tests

pytestmark = pytest.mark.live

load_local_dotenv_for_live_tests()


def _live_enabled() -> bool:
    return os.getenv("AGENT_DRIVER_RUN_LIVE_TESTS", "").strip() == "1"


def _resolve_live_provider():
    model = os.getenv("AGENT_DRIVER_MODEL")
    api_key = os.getenv("AGENT_DRIVER_API_KEY")
    if not model or not api_key:
        pytest.skip("OpenRouter live env is not configured")
    return resolve_provider(
        ProviderSpec(
            provider_id="openrouter",
            model=model,
            base_url=os.getenv("AGENT_DRIVER_BASE_URL") or None,
            api_key=api_key,
            timeout_s=120.0,
        )
    )


@pytest.mark.asyncio
async def test_live_real_task_file_and_python() -> None:
    """Agent computes with the sandboxed Python tool and persists exact results.

    The numbers are ones the model cannot reliably guess without executing
    code, so a correct results.txt proves the Python tool really ran and the
    filesystem tools really wrote to the workspace.
    """
    if not _live_enabled():
        pytest.skip("live tests disabled")
    provider = _resolve_live_provider()
    workspace = Path(tempfile.mkdtemp(prefix="ad_live_py_"))
    agent = create_agent(
        provider=provider,
        tools=ToolSet.only("python", "file_write", "read_file"),
        config=RunnerConfig(python_tool=PythonToolSettings(enabled=True)),
    )
    output = await agent.run(
        AgentRunInput(
            input=(
                "Use the python tool to compute the sum of squares of the integers "
                "from 1 to 47, and separately how many primes are below 100. Then "
                "write both numbers, one per line as 'sum_of_squares=<n>' and "
                "'primes_below_100=<n>', into results.txt. Report the two values."
            ),
            run_id="run_live_real_python",
            agent_id="agent.live",
            thread_id="t-live-python",
            graph_preset="single_react",
            max_steps=22,
            max_tool_calls=14,
            deadline_seconds=170.0,
            app_metadata={"workspace_cwd": str(workspace)},
        )
    )

    assert output.status.value == "completed"
    results = workspace / "results.txt"
    assert results.exists(), "agent did not write results.txt to the workspace"
    text = results.read_text(encoding="utf-8")
    # Ground truth: sum_{1..47} k^2 = 47*48*95/6 = 35720; primes below 100 = 25.
    assert "sum_of_squares=35720" in text
    assert "primes_below_100=25" in text


@pytest.mark.asyncio
async def test_live_real_task_find_and_fix_bug() -> None:
    """Agent runs a realistic debugging loop over a seeded project.

    Exercises discovery (grep/glob), reading, an edit, and Python verification.
    The planted bug (``add`` returns ``a - b``) must end up corrected on disk.
    """
    if not _live_enabled():
        pytest.skip("live tests disabled")
    provider = _resolve_live_provider()
    workspace = Path(tempfile.mkdtemp(prefix="ad_live_fix_"))
    (workspace / "utils.py").write_text(
        "def greet(n):\n    return 'hi ' + n\n", encoding="utf-8"
    )
    (workspace / "mathlib.py").write_text(
        "def add(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a * b\n",
        encoding="utf-8",
    )
    (workspace / "README.md").write_text("# demo project\n", encoding="utf-8")

    agent = create_agent(
        provider=provider,
        tools=ToolSet.only(
            "python",
            "file_write",
            "file_edit",
            "read_file",
            "glob_search",
            "grep_search",
        ),
        config=RunnerConfig(python_tool=PythonToolSettings(enabled=True)),
    )
    output = await agent.run(
        AgentRunInput(
            input=(
                "This workspace is a small Python project. One file defines a "
                "function `add(a, b)` that is supposed to return the sum of its two "
                "arguments, but it has a bug. Find the file, fix the bug, and report "
                "which file you fixed."
            ),
            run_id="run_live_real_fix",
            agent_id="agent.live",
            thread_id="t-live-fix",
            graph_preset="single_react",
            max_steps=24,
            max_tool_calls=16,
            deadline_seconds=170.0,
            app_metadata={"workspace_cwd": str(workspace)},
        )
    )

    assert output.status.value == "completed"
    fixed = (workspace / "mathlib.py").read_text(encoding="utf-8")
    assert "return a + b" in fixed, "agent did not correct the planted add() bug"
    assert "return a - b" not in fixed, "the buggy subtraction is still present"
