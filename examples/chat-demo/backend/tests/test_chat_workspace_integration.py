"""Integration: agent run writes files into session workspace."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_driver.contracts import AgentRunInput, ChatMessage
from agent_driver.contracts.tools import ToolCall
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmResponse, UsageSummary
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import create_runtime_store_bundle, runtime_store_config_from_env
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet

from app.deps import get_settings, reset_dependency_caches
from app.services.agent_factory import AgentBundle
from app.workspace import build_chat_app_metadata, resolve_session_workspace


class _FileWriteOnce(FakeProvider):
    def __init__(self) -> None:
        super().__init__(response_text="done")
        self.calls = 0

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.calls += 1
        if self.calls == 1:
            return LlmResponse(
                message=ChatMessage(role="assistant", content=""),
                finish_reason=LlmFinishReason.TOOL_CALLS,
                usage=UsageSummary(model_provider="fake", model_name="test"),
                provider="fake",
                model="test-model",
                metadata={
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="file_write",
                            args={
                                "path": "agent_notes.txt",
                                "content": "hello workspace\n",
                            },
                        ).model_dump(mode="json")
                    ]
                },
            )
        return await super().complete(request)


def _workspace_bundle(tmp_path, monkeypatch) -> AgentBundle:
    ws_root = tmp_path / "workspace"
    monkeypatch.setenv("AGENT_DRIVER_PROVIDER", "fake")
    monkeypatch.setenv("AGENT_DRIVER_RUNTIME_STORE_KIND", "memory")
    monkeypatch.setenv("CHAT_DEMO_TOOL_PRESET", "dev")
    monkeypatch.setenv("CHAT_DEMO_SESSIONS_PATH", str(tmp_path / "sessions.json"))
    monkeypatch.setenv("CHAT_DEMO_WORKSPACE_ROOT", str(ws_root))
    reset_dependency_caches()
    settings = get_settings()
    provider = _FileWriteOnce()
    toolset = ToolSet.only("file_write")
    runtime_store_bundle = create_runtime_store_bundle(runtime_store_config_from_env())
    agent = create_agent(
        provider=provider,
        tools=toolset,
        checkpoint_store=runtime_store_bundle.checkpoint_store,
        event_log=runtime_store_bundle.event_log,
    )
    from agent_driver.cli.sessions import SessionStore

    registry = agent.runner.config.tool_registry
    manifests = tuple(item.manifest for item in registry.list_registered()) if registry else ()
    return AgentBundle(
        agent=agent,
        event_log=runtime_store_bundle.event_log,
        checkpoint_store=runtime_store_bundle.checkpoint_store,
        session_store=SessionStore(path=Path(settings.sessions_path)),
        manifests=manifests,
        store_kind="memory",
    )


@pytest.mark.asyncio
async def test_agent_run_writes_file_under_session_workspace(tmp_path, monkeypatch) -> None:
    bundle = _workspace_bundle(tmp_path, monkeypatch)
    settings = get_settings()
    session_id = "session_ws_test"
    await bundle.agent.run(
        AgentRunInput(
            input="write notes",
            run_id="run_ws_test",
            thread_id="thread_ws_test",
            agent_id="chat-demo-agent",
            graph_preset="single_react",
            app_metadata=build_chat_app_metadata(settings, session_id),
        )
    )

    expected_dir = resolve_session_workspace(settings, session_id)
    written = expected_dir / "agent_notes.txt"
    assert written.is_file()
    assert written.read_text(encoding="utf-8") == "hello workspace\n"
    backend_cwd_file = Path.cwd() / "agent_notes.txt"
    assert not backend_cwd_file.is_file()

    reset_dependency_caches()
