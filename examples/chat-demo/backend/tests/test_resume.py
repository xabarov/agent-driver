from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from agent_driver.contracts import AgentRunInput, ChatMessage
from agent_driver.contracts.enums import ResumeAction, ToolRisk
from agent_driver.llm.contracts import LlmFinishReason
from agent_driver.contracts.tools import ToolCall
from agent_driver.llm.contracts import LlmRequest, LlmResponse, UsageSummary
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import create_runtime_store_bundle, runtime_store_config_from_env
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet

from app.deps import reset_dependency_caches
from app.main import create_app
from app.services.agent_factory import AgentBundle


class _InterruptThenStop(FakeProvider):
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
                            args={"path": "/tmp/resume-test.txt", "content": "approved\n"},
                        ).model_dump(mode="json")
                    ]
                },
            )
        return LlmResponse(
            message=ChatMessage(role="assistant", content="write completed"),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="fake", model_name="test"),
            provider="fake",
            model="test-model",
        )


def _interrupt_bundle(tmp_path, monkeypatch) -> AgentBundle:
    monkeypatch.setenv("AGENT_DRIVER_PROVIDER", "fake")
    monkeypatch.setenv("AGENT_DRIVER_RUNTIME_STORE_KIND", "memory")
    monkeypatch.setenv("CHAT_DEMO_TOOL_PRESET", "dev")
    monkeypatch.setenv("CHAT_DEMO_SESSIONS_PATH", str(tmp_path / "sessions.json"))
    reset_dependency_caches()
    settings = __import__("app.deps", fromlist=["get_settings"]).get_settings()
    provider = _InterruptThenStop()
    toolset = ToolSet.only("file_write")
    runtime_store_bundle = create_runtime_store_bundle(runtime_store_config_from_env())
    agent = create_agent(
        provider=provider,
        tools=toolset,
        checkpoint_store=runtime_store_bundle.checkpoint_store,
        event_log=runtime_store_bundle.event_log,
    )
    from agent_driver.cli.sessions import SessionStore
    from pathlib import Path

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
async def test_resume_approve_streams_terminal_event(tmp_path, monkeypatch) -> None:
    bundle = _interrupt_bundle(tmp_path, monkeypatch)
    paused = await bundle.agent.run(
        AgentRunInput(
            input="write file",
            run_id="run_resume_api_test",
            thread_id="thread_resume_api_test",
            agent_id="chat-demo-agent",
            graph_preset="single_react",
            tool_policy={"approval_required_for_risk": ToolRisk.MEDIUM.value},
        )
    )
    assert paused.status.value == "paused"
    assert paused.interrupt is not None

    application = create_app()
    from app.api.chat import get_resume_bundle

    application.dependency_overrides[get_resume_bundle] = lambda _body=None: bundle

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        events: list[str] = []
        async with client.stream(
            "POST",
            "/api/chat/runs/run_resume_api_test/resume",
            json={
                "interrupt_id": paused.interrupt.interrupt_id,
                "action": ResumeAction.APPROVE.value,
            },
        ) as stream_response:
            assert stream_response.status_code == 200
            async for line in stream_response.aiter_lines():
                if line.startswith("event: "):
                    events.append(line.removeprefix("event: ").strip())
                if line == "event: run_completed":
                    break

    assert "run_completed" in events
    reset_dependency_caches()
