"""Optional live smoke: runner + OpenAI-compatible provider + built-in tools."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from agent_driver.contracts import (
    AgentRunInput,
    ResumeAction,
    ResumeCommand,
    ToolCall,
    ToolRisk,
)
from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.openai_compatible import OpenAICompatibleProvider
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
    wrap_governed_executor,
)
from agent_driver.tools import GovernedToolExecutor, ToolRegistry, register_builtin_tools
from tests.live_env import load_local_dotenv_for_live_tests

pytestmark = pytest.mark.live


class _RequestMetadataEchoProvider:
    """Wrap live provider and echo request metadata back into response metadata."""

    def __init__(self, provider: OpenAICompatibleProvider) -> None:
        self._provider = provider

    @property
    def name(self) -> str:
        """Expose wrapped provider name for runtime events."""
        return self._provider.name

    async def healthcheck(self):
        """Delegate health probe to wrapped provider."""
        return await self._provider.healthcheck()

    async def complete(self, request: LlmRequest) -> LlmResponse:
        """Delegate completion and preserve request metadata for tool planning."""
        response = await self._provider.complete(request)
        return response.model_copy(
            update={"metadata": {**response.metadata, **request.metadata}}
        )

    async def stream(self, request: LlmRequest):
        """Delegate streaming without metadata mutation."""
        async for event in self._provider.stream(request):
            yield event


def _live_enabled() -> bool:
    return os.getenv("AGENT_DRIVER_RUN_LIVE_TESTS", "").strip() == "1"


def _env(name: str, fallback: str | None = None) -> str | None:
    """Resolve env var from AGENT_DRIVER_* or legacy OpenRouter names."""
    value = os.getenv(name)
    if value:
        return value
    legacy_map = {
        "AGENT_DRIVER_OPENAI_BASE_URL": "OPENROUTER_BASE_URL",
        "AGENT_DRIVER_OPENAI_API_KEY": "OPENROUTER_API_KEY",
        "AGENT_DRIVER_OPENAI_MODEL": "OPENROUTER_MODEL",
    }
    legacy = legacy_map.get(name)
    if legacy:
        legacy_value = os.getenv(legacy)
        if legacy_value:
            return legacy_value
    return fallback


load_local_dotenv_for_live_tests()


def _tool_result(output, tool_name: str) -> dict[str, Any]:
    """Return first tool result envelope by tool name."""
    tool_results = output.metadata.get("tool_results", [])
    if not isinstance(tool_results, list):
        return {}
    for row in tool_results:
        if not isinstance(row, dict):
            continue
        call = row.get("call")
        if isinstance(call, dict) and call.get("tool_name") == tool_name:
            return row
    return {}


def _notebook_fixture(path: Path) -> None:
    """Create minimal one-cell notebook fixture."""
    payload = {
        "cells": [
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": ["print('old')\n"],
            }
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")


def _build_live_runner(base_url: str, model: str, api_key: str | None) -> FakeSingleStepRunner:
    """Build live runner with governed built-in registry."""
    provider = _RequestMetadataEchoProvider(
        OpenAICompatibleProvider(
            config=OpenAICompatibleProvider.Config(
                name="openai-live",
                base_url=base_url,
                api_key=api_key,
                model=model,
            )
        )
    )
    registry = ToolRegistry()
    register_builtin_tools(registry)
    return FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            )
        ),
    )


def _assert_live_interrupt_for_tool(output, tool_name: str) -> None:
    """Assert paused output and interrupt metadata for tool-gated live lane."""
    assert output.status.value == "paused"
    assert output.interrupt is not None
    assert output.interrupt.reason.value == "approval_required"
    approval_payload = output.metadata.get("approval_payload")
    assert isinstance(approval_payload, dict)
    assert approval_payload.get("tool_name") == tool_name
    assert any(
        item.tool_name == tool_name and item.status.value == "denied"
        for item in output.tool_trace
    )
    envelope = _tool_result(output, tool_name)
    assert envelope
    assert envelope["decision"] == "interrupt"


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _live_enabled(), reason="live tests require AGENT_DRIVER_RUN_LIVE_TESTS=1"
)
async def test_live_agent_run_with_governed_builtin_tool_call() -> None:
    """Run one live LLM call plus one deterministic built-in tool stage."""
    base_url = _env("AGENT_DRIVER_OPENAI_BASE_URL")
    model = _env("AGENT_DRIVER_OPENAI_MODEL")
    api_key = _env("AGENT_DRIVER_OPENAI_API_KEY")
    if not base_url or not model:
        pytest.skip(
            "AGENT_DRIVER_OPENAI_BASE_URL and AGENT_DRIVER_OPENAI_MODEL are required"
        )
    runner = _build_live_runner(base_url=base_url, model=model, api_key=api_key)
    output = await runner.run(
        AgentRunInput(
            input="Say hello in one short sentence.",
            run_id="run_live_agent_tool_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="web_search",
                            args={
                                "query": "agent driver runtime",
                                "mock_results": [
                                    {
                                        "title": "Agent Driver",
                                        "url": "https://example.com",
                                        "snippet": "runtime",
                                    }
                                ],
                            },
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    assert output.status.value == "completed"
    envelope = _tool_result(output, "web_search")
    assert envelope
    assert envelope["decision"] == "allow"
    assert isinstance(envelope.get("structured_output"), dict)
    tool_trace = output.tool_trace
    assert any(
        item.tool_name == "web_search" and item.status.value == "completed"
        for item in tool_trace
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _live_enabled(), reason="live tests require AGENT_DRIVER_RUN_LIVE_TESTS=1"
)
async def test_live_agent_run_with_governed_builtin_bash_call() -> None:
    """Run live LLM call plus real governed bash tool execution."""
    base_url = _env("AGENT_DRIVER_OPENAI_BASE_URL")
    model = _env("AGENT_DRIVER_OPENAI_MODEL")
    api_key = _env("AGENT_DRIVER_OPENAI_API_KEY")
    if not base_url or not model:
        pytest.skip(
            "AGENT_DRIVER_OPENAI_BASE_URL and AGENT_DRIVER_OPENAI_MODEL are required"
        )
    runner = _build_live_runner(base_url=base_url, model=model, api_key=api_key)
    output = await runner.run(
        AgentRunInput(
            input="Reply with one short sentence about shell verification.",
            run_id="run_live_agent_tool_bash_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="bash",
                            args={
                                "command": "echo live-bash-smoke",
                                "timeout_seconds": 5,
                            },
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    assert output.status.value == "completed"
    envelope = _tool_result(output, "bash")
    assert envelope
    assert envelope["decision"] == "allow"
    structured = envelope.get("structured_output")
    assert isinstance(structured, dict)
    assert structured.get("exit_code") == 0
    assert structured.get("timed_out") is False
    stdout = str(structured.get("stdout") or "")
    assert "live-bash-smoke" in stdout
    tool_trace = output.tool_trace
    assert any(
        item.tool_name == "bash" and item.status.value == "completed"
        for item in tool_trace
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _live_enabled(), reason="live tests require AGENT_DRIVER_RUN_LIVE_TESTS=1"
)
async def test_live_agent_run_with_governed_builtin_notebook_edit_call(
    tmp_path,
) -> None:
    """Run live LLM call plus real notebook_edit on temp .ipynb."""
    base_url = _env("AGENT_DRIVER_OPENAI_BASE_URL")
    model = _env("AGENT_DRIVER_OPENAI_MODEL")
    api_key = _env("AGENT_DRIVER_OPENAI_API_KEY")
    if not base_url or not model:
        pytest.skip(
            "AGENT_DRIVER_OPENAI_BASE_URL and AGENT_DRIVER_OPENAI_MODEL are required"
        )
    target = tmp_path / "live_notebook.ipynb"
    _notebook_fixture(target)
    runner = _build_live_runner(base_url=base_url, model=model, api_key=api_key)
    output = await runner.run(
        AgentRunInput(
            input="Reply with one short sentence about notebook edit verification.",
            run_id="run_live_agent_tool_notebook_edit_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="notebook_edit",
                            args={
                                "path": str(target),
                                "cell_idx": 0,
                                "is_new_cell": False,
                                "old_text": "old",
                                "new_text": "new",
                            },
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    assert output.status.value == "completed"
    envelope = _tool_result(output, "notebook_edit")
    assert envelope
    assert envelope["decision"] == "allow"
    structured = envelope.get("structured_output")
    assert isinstance(structured, dict)
    assert structured.get("operation") == "replace"
    rendered = json.loads(target.read_text(encoding="utf-8"))
    assert rendered["cells"][0]["source"] == ["print('new')\n"]
    assert any(
        item.tool_name == "notebook_edit" and item.status.value == "completed"
        for item in output.tool_trace
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _live_enabled(), reason="live tests require AGENT_DRIVER_RUN_LIVE_TESTS=1"
)
async def test_live_agent_run_with_governed_builtin_file_write_call(tmp_path) -> None:
    """Run live LLM call plus real file_write side-effect on temp file."""
    base_url = _env("AGENT_DRIVER_OPENAI_BASE_URL")
    model = _env("AGENT_DRIVER_OPENAI_MODEL")
    api_key = _env("AGENT_DRIVER_OPENAI_API_KEY")
    if not base_url or not model:
        pytest.skip(
            "AGENT_DRIVER_OPENAI_BASE_URL and AGENT_DRIVER_OPENAI_MODEL are required"
        )
    target = tmp_path / "live_write.txt"
    runner = _build_live_runner(base_url=base_url, model=model, api_key=api_key)
    output = await runner.run(
        AgentRunInput(
            input="Reply with one short sentence about file write verification.",
            run_id="run_live_agent_tool_file_write_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="file_write",
                            args={"path": str(target), "content": "live-write\n"},
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    assert output.status.value == "completed"
    envelope = _tool_result(output, "file_write")
    assert envelope
    assert envelope["decision"] == "allow"
    structured = envelope.get("structured_output")
    assert isinstance(structured, dict)
    assert structured.get("mode") == "overwrite"
    assert target.read_text(encoding="utf-8") == "live-write\n"
    assert any(
        item.tool_name == "file_write" and item.status.value == "completed"
        for item in output.tool_trace
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _live_enabled(), reason="live tests require AGENT_DRIVER_RUN_LIVE_TESTS=1"
)
async def test_live_agent_run_with_governed_builtin_file_edit_call(tmp_path) -> None:
    """Run live LLM call plus real file_edit side-effect on temp file."""
    base_url = _env("AGENT_DRIVER_OPENAI_BASE_URL")
    model = _env("AGENT_DRIVER_OPENAI_MODEL")
    api_key = _env("AGENT_DRIVER_OPENAI_API_KEY")
    if not base_url or not model:
        pytest.skip(
            "AGENT_DRIVER_OPENAI_BASE_URL and AGENT_DRIVER_OPENAI_MODEL are required"
        )
    target = tmp_path / "live_edit.txt"
    target.write_text("alpha-old\n", encoding="utf-8")
    runner = _build_live_runner(base_url=base_url, model=model, api_key=api_key)
    output = await runner.run(
        AgentRunInput(
            input="Reply with one short sentence about file edit verification.",
            run_id="run_live_agent_tool_file_edit_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="file_edit",
                            args={
                                "path": str(target),
                                "old_text": "old",
                                "new_text": "new",
                            },
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    assert output.status.value == "completed"
    envelope = _tool_result(output, "file_edit")
    assert envelope
    assert envelope["decision"] == "allow"
    structured = envelope.get("structured_output")
    assert isinstance(structured, dict)
    assert structured.get("replacements") == 1
    assert target.read_text(encoding="utf-8") == "alpha-new\n"
    assert any(
        item.tool_name == "file_edit" and item.status.value == "completed"
        for item in output.tool_trace
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _live_enabled(), reason="live tests require AGENT_DRIVER_RUN_LIVE_TESTS=1"
)
async def test_live_agent_run_interrupts_for_bash_when_approval_required() -> None:
    """Live lane should pause before bash when risk threshold requires approval."""
    base_url = _env("AGENT_DRIVER_OPENAI_BASE_URL")
    model = _env("AGENT_DRIVER_OPENAI_MODEL")
    api_key = _env("AGENT_DRIVER_OPENAI_API_KEY")
    if not base_url or not model:
        pytest.skip(
            "AGENT_DRIVER_OPENAI_BASE_URL and AGENT_DRIVER_OPENAI_MODEL are required"
        )
    runner = _build_live_runner(base_url=base_url, model=model, api_key=api_key)
    output = await runner.run(
        AgentRunInput(
            input="Reply briefly.",
            run_id="run_live_agent_tool_bash_interrupt",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "approval_required_for_risk": ToolRisk.MEDIUM.value,
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="bash",
                            args={"command": "echo should-not-run"},
                        ).model_dump(mode="json")
                    ]
                },
            },
        )
    )
    _assert_live_interrupt_for_tool(output, "bash")


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _live_enabled(), reason="live tests require AGENT_DRIVER_RUN_LIVE_TESTS=1"
)
async def test_live_agent_run_interrupts_for_file_write_when_approval_required(
    tmp_path,
) -> None:
    """Live lane should pause before file_write when risk threshold requires approval."""
    base_url = _env("AGENT_DRIVER_OPENAI_BASE_URL")
    model = _env("AGENT_DRIVER_OPENAI_MODEL")
    api_key = _env("AGENT_DRIVER_OPENAI_API_KEY")
    if not base_url or not model:
        pytest.skip(
            "AGENT_DRIVER_OPENAI_BASE_URL and AGENT_DRIVER_OPENAI_MODEL are required"
        )
    target = tmp_path / "blocked-write.txt"
    runner = _build_live_runner(base_url=base_url, model=model, api_key=api_key)
    output = await runner.run(
        AgentRunInput(
            input="Reply briefly.",
            run_id="run_live_agent_tool_file_write_interrupt",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "approval_required_for_risk": ToolRisk.MEDIUM.value,
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="file_write",
                            args={"path": str(target), "content": "blocked\n"},
                        ).model_dump(mode="json")
                    ]
                },
            },
        )
    )
    _assert_live_interrupt_for_tool(output, "file_write")
    assert not target.exists()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _live_enabled(), reason="live tests require AGENT_DRIVER_RUN_LIVE_TESTS=1"
)
async def test_live_agent_run_resume_approve_executes_pending_file_write(
    tmp_path,
) -> None:
    """Live HITL lane: approve resume should execute pending file_write once."""
    base_url = _env("AGENT_DRIVER_OPENAI_BASE_URL")
    model = _env("AGENT_DRIVER_OPENAI_MODEL")
    api_key = _env("AGENT_DRIVER_OPENAI_API_KEY")
    if not base_url or not model:
        pytest.skip(
            "AGENT_DRIVER_OPENAI_BASE_URL and AGENT_DRIVER_OPENAI_MODEL are required"
        )
    target = tmp_path / "resume-approve.txt"
    runner = _build_live_runner(base_url=base_url, model=model, api_key=api_key)
    paused = await runner.run(
        AgentRunInput(
            input="Reply briefly.",
            run_id="run_live_agent_tool_file_write_resume_approve",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "approval_required_for_risk": ToolRisk.MEDIUM.value,
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="file_write",
                            args={"path": str(target), "content": "approved\n"},
                        ).model_dump(mode="json")
                    ]
                },
            },
        )
    )
    _assert_live_interrupt_for_tool(paused, "file_write")
    assert paused.interrupt is not None
    resumed = await runner.run(
        AgentRunInput(
            run_id="run_live_agent_tool_file_write_resume_approve",
            resume=ResumeCommand(
                interrupt_id=paused.interrupt.interrupt_id,
                action=ResumeAction.APPROVE,
            ),
            agent_id="agent.live",
            graph_preset="single_react",
        )
    )
    assert resumed.status.value == "completed"
    assert target.read_text(encoding="utf-8") == "approved\n"
    assert any(
        item.tool_name == "file_write" and item.status.value == "completed"
        for item in resumed.tool_trace
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _live_enabled(), reason="live tests require AGENT_DRIVER_RUN_LIVE_TESTS=1"
)
async def test_live_agent_run_resume_reject_blocks_pending_file_write(tmp_path) -> None:
    """Live HITL lane: reject resume should keep side effect unapplied."""
    base_url = _env("AGENT_DRIVER_OPENAI_BASE_URL")
    model = _env("AGENT_DRIVER_OPENAI_MODEL")
    api_key = _env("AGENT_DRIVER_OPENAI_API_KEY")
    if not base_url or not model:
        pytest.skip(
            "AGENT_DRIVER_OPENAI_BASE_URL and AGENT_DRIVER_OPENAI_MODEL are required"
        )
    target = tmp_path / "resume-reject.txt"
    runner = _build_live_runner(base_url=base_url, model=model, api_key=api_key)
    paused = await runner.run(
        AgentRunInput(
            input="Reply briefly.",
            run_id="run_live_agent_tool_file_write_resume_reject",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "approval_required_for_risk": ToolRisk.MEDIUM.value,
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="file_write",
                            args={"path": str(target), "content": "rejected\n"},
                        ).model_dump(mode="json")
                    ]
                },
            },
        )
    )
    _assert_live_interrupt_for_tool(paused, "file_write")
    assert paused.interrupt is not None
    rejected = await runner.run(
        AgentRunInput(
            run_id="run_live_agent_tool_file_write_resume_reject",
            resume=ResumeCommand(
                interrupt_id=paused.interrupt.interrupt_id,
                action=ResumeAction.REJECT,
            ),
            agent_id="agent.live",
            graph_preset="single_react",
        )
    )
    assert rejected.status.value == "failed"
    assert rejected.terminal_reason is not None
    assert rejected.terminal_reason.value == "approval_rejected"
    assert not target.exists()
