from __future__ import annotations

import pytest
from app.deps import reset_dependency_caches
from app.main import create_app
from app.services.agent_factory import AgentBundle
from httpx import ASGITransport, AsyncClient

from agent_driver.contracts import AgentRunInput, ChatMessage
from agent_driver.contracts.enums import (
    ApprovalMode,
    ResumeAction,
    SideEffectClass,
    ToolRisk,
)
from agent_driver.contracts.tools import ToolCall, ToolManifest
from agent_driver.llm.contracts import (
    LlmFinishReason,
    LlmRequest,
    LlmResponse,
    UsageSummary,
)
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import (
    create_runtime_store_bundle,
    runtime_store_config_from_env,
)
from agent_driver.runtime.control import InMemoryCommandQueueStore
from agent_driver.runtime.single_agent.types import RunnerConfig
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolRegistry, ToolSet


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
                            tool_name="approval_test",
                            args={"message": "approved"},
                        ).model_dump(mode="json")
                    ]
                },
            )
        return LlmResponse(
            message=ChatMessage(role="assistant", content="approval completed"),
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
    registry = ToolRegistry()

    async def approval_test_tool(args: dict[str, object]) -> dict[str, object]:
        return {"ok": True, "message": str(args.get("message", ""))}

    registry.register(
        ToolManifest(
            name="approval_test",
            description="Test-only approval tool for resume API coverage.",
            risk=ToolRisk.MEDIUM,
            side_effect=SideEffectClass.EXTERNAL_ACTION,
            approval_mode=ApprovalMode.ON_POLICY_MATCH,
        ),
        approval_test_tool,
    )
    toolset = ToolSet.only("approval_test")
    runtime_store_bundle = create_runtime_store_bundle(runtime_store_config_from_env())
    agent = create_agent(
        provider=provider,
        tools=toolset,
        config=RunnerConfig(tool_registry=registry),
        checkpoint_store=runtime_store_bundle.checkpoint_store,
        event_log=runtime_store_bundle.event_log,
    )
    from pathlib import Path

    from agent_driver.cli.sessions import SessionStore

    registry = agent.runner.config.tool_registry
    manifests = (
        tuple(item.manifest for item in registry.list_registered()) if registry else ()
    )
    return AgentBundle(
        agent=agent,
        event_log=runtime_store_bundle.event_log,
        checkpoint_store=runtime_store_bundle.checkpoint_store,
        command_queue_store=InMemoryCommandQueueStore(),
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


@pytest.mark.asyncio
async def test_fake_plan_approval_scenario_streams_interrupt_and_resume(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("AGENT_DRIVER_PROVIDER", "fake")
    monkeypatch.setenv("CHAT_DEMO_FAKE_SCENARIO", "plan_approval")
    monkeypatch.setenv("AGENT_DRIVER_RUNTIME_STORE_KIND", "memory")
    monkeypatch.setenv("CHAT_DEMO_TOOL_PRESET", "dev")
    monkeypatch.setenv("CHAT_DEMO_SESSIONS_PATH", str(tmp_path / "sessions.json"))
    reset_dependency_caches()
    application = create_app()

    run_id = ""
    interrupt_id = ""
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        async with client.stream(
            "POST",
            "/api/chat/messages",
            json={
                "message": "please plan this change",
                "tool_preset": "dev",
                "force_planning": True,
            },
        ) as stream_response:
            assert stream_response.status_code == 200
            run_id = stream_response.headers["x-run-id"]
            events: list[str] = []
            async for line in stream_response.aiter_lines():
                if line.startswith("event: "):
                    event = line.removeprefix("event: ").strip()
                    events.append(event)
                    if event == "interrupt_requested":
                        break
        assert "interrupt_requested" in events
        from app.deps import get_agent_bundle_for_request

        checkpoint = get_agent_bundle_for_request("dev").checkpoint_store.latest(run_id)
        assert checkpoint is not None
        policy_metadata = checkpoint.state.run_input.tool_policy.metadata
        assert policy_metadata["force_planning"]["enabled"] is True

        interrupt_response = await client.get(f"/api/chat/runs/{run_id}/interrupt")
        assert interrupt_response.status_code == 200
        interrupt = interrupt_response.json()
        interrupt_id = interrupt["interrupt_id"]
        assert interrupt["reason"] == "plan_approval_required"
        assert interrupt["proposed_action"]["plan_approval"]["content"]

        resumed_events: list[str] = []
        async with client.stream(
            "POST",
            f"/api/chat/runs/{run_id}/resume",
            json={"interrupt_id": interrupt_id, "action": ResumeAction.APPROVE.value},
        ) as resume_response:
            assert resume_response.status_code == 200
            async for line in resume_response.aiter_lines():
                if line.startswith("event: "):
                    event = line.removeprefix("event: ").strip()
                    resumed_events.append(event)
                    if event == "run_completed":
                        break

    assert "run_resumed" in resumed_events
    assert "run_completed" in resumed_events
    reset_dependency_caches()


@pytest.mark.asyncio
async def test_fake_force_planning_block_scenario_streams_denied_tool(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("AGENT_DRIVER_PROVIDER", "fake")
    monkeypatch.setenv("CHAT_DEMO_FAKE_SCENARIO", "force_planning_block")
    monkeypatch.setenv("AGENT_DRIVER_RUNTIME_STORE_KIND", "memory")
    monkeypatch.setenv("CHAT_DEMO_TOOL_PRESET", "dev")
    monkeypatch.setenv("CHAT_DEMO_SESSIONS_PATH", str(tmp_path / "sessions.json"))
    reset_dependency_caches()
    application = create_app()

    event_names: list[str] = []
    event_data: list[str] = []
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        async with client.stream(
            "POST",
            "/api/chat/messages",
            json={
                "message": "try a write before planning",
                "tool_preset": "dev",
                "force_planning": True,
            },
        ) as stream_response:
            assert stream_response.status_code == 200
            async for line in stream_response.aiter_lines():
                if line.startswith("event: "):
                    event = line.removeprefix("event: ").strip()
                    event_names.append(event)
                    if event == "run_completed":
                        break
                if line.startswith("data: "):
                    event_data.append(line.removeprefix("data: ").strip())

    assert "tool_call_completed" in event_names
    assert "run_completed" in event_names
    joined_data = "\n".join(event_data)
    assert '"tool_name": "file_write"' in joined_data
    assert '"status": "denied"' in joined_data
    assert "force planning requires an approved plan" in joined_data
    reset_dependency_caches()
