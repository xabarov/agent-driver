from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.deps import reset_dependency_caches
from app.main import create_app


async def _collect_chat_events(
    *,
    message: str,
    research_depth: str | None = None,
) -> list[dict[str, object]]:
    app = create_app()
    events: list[dict[str, object]] = []
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        payload: dict[str, object] = {"message": message}
        if research_depth is not None:
            payload["research_depth"] = research_depth
        async with client.stream(
            "POST",
            "/api/chat/messages",
            json=payload,
        ) as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    event = json.loads(line.removeprefix("data: "))
                    events.append(event)
                    if event.get("event") in {
                        "run_completed",
                        "run_failed",
                        "run_cancelled",
                    }:
                        break
    return events


@pytest.mark.asyncio
async def test_chat_deep_research_sse_emits_skill_and_ledger(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_DRIVER_PROVIDER", "fake")
    monkeypatch.setenv("CHAT_DEMO_FAKE_SCENARIO", "deep_research_skills")
    monkeypatch.setenv("AGENT_DRIVER_RUNTIME_STORE_KIND", "memory")
    monkeypatch.setenv("CHAT_DEMO_TOOL_PRESET", "web")
    monkeypatch.setenv("CHAT_DEMO_SESSIONS_PATH", str(tmp_path / "sessions.json"))
    monkeypatch.setenv("CHAT_DEMO_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    reset_dependency_caches()
    try:
        events = await _collect_chat_events(
            message="run a deep research skill probe",
            research_depth="deep_parallel_research",
        )
    finally:
        reset_dependency_caches()

    names = [event["event"] for event in events]
    assert "skill_invoked" in names
    assert "source_ledger_updated" in names
    ledger = next(
        event for event in events if event["event"] == "source_ledger_updated"
    )
    data = ledger["data"]
    assert isinstance(data, dict)
    assert data["verified_reads"]


@pytest.mark.asyncio
async def test_chat_deep_research_sse_emits_report_artifact(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_DRIVER_PROVIDER", "fake")
    monkeypatch.setenv("CHAT_DEMO_FAKE_SCENARIO", "deep_research_artifact")
    monkeypatch.setenv("AGENT_DRIVER_RUNTIME_STORE_KIND", "memory")
    monkeypatch.setenv("CHAT_DEMO_TOOL_PRESET", "web")
    monkeypatch.setenv("CHAT_DEMO_SESSIONS_PATH", str(tmp_path / "sessions.json"))
    workspace_root = tmp_path / "workspace"
    monkeypatch.setenv("CHAT_DEMO_WORKSPACE_ROOT", str(workspace_root))
    reset_dependency_caches()
    try:
        events = await _collect_chat_events(
            message="run a deep research artifact probe",
            research_depth="deep_parallel_research",
        )
    finally:
        reset_dependency_caches()

    names = [event["event"] for event in events]
    assert "artifact_created" in names
    artifact_events = [
        event
        for event in events
        if event["event"] in {"artifact_created", "artifact_updated"}
    ]
    assert any(
        isinstance(event["data"], dict)
        and event["data"].get("path") == "research/sources.jsonl"
        for event in artifact_events
    )
    artifact_event = next(
        event
        for event in artifact_events
        if isinstance(event["data"], dict)
        and event["data"].get("path") == "research/report.md"
    )
    data = artifact_event["data"]
    assert isinstance(data, dict)
    assert data["path"] == "research/report.md"
    assert data["tool_name"] == "file_write"
    completed = next(event for event in events if event["event"] == "run_completed")
    metadata = completed["data"]
    assert isinstance(metadata, dict)
    artifacts = metadata["deep_research_artifacts"]
    assert isinstance(artifacts, dict)
    assert artifacts["report_path"] == "research/report.md"
    assert artifacts["source_ledger_path"] == "research/sources.jsonl"


@pytest.mark.asyncio
async def test_chat_deep_research_sse_emits_blocked_fetch_ledger(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_DRIVER_PROVIDER", "fake")
    monkeypatch.setenv("CHAT_DEMO_FAKE_SCENARIO", "deep_research_blocked_fetch")
    monkeypatch.setenv("AGENT_DRIVER_RUNTIME_STORE_KIND", "memory")
    monkeypatch.setenv("CHAT_DEMO_TOOL_PRESET", "web")
    monkeypatch.setenv("CHAT_DEMO_SESSIONS_PATH", str(tmp_path / "sessions.json"))
    monkeypatch.setenv("CHAT_DEMO_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    reset_dependency_caches()
    try:
        events = await _collect_chat_events(
            message="run a blocked fetch deep research artifact probe",
            research_depth="deep_parallel_research",
        )
    finally:
        reset_dependency_caches()

    ledger = next(
        event for event in events if event["event"] == "source_ledger_updated"
    )
    data = ledger["data"]
    assert isinstance(data, dict)
    assert len(data["blocked_reads"]) == 2
    assert not data["verified_reads"]
    completed = next(event for event in events if event["event"] == "run_completed")
    metadata = completed["data"]
    assert isinstance(metadata, dict)
    artifacts = metadata["deep_research_artifacts"]
    assert isinstance(artifacts, dict)
    assert artifacts["report_path"] == "research/report.md"
    assert artifacts["source_ledger_path"] == "research/sources.jsonl"


@pytest.mark.asyncio
async def test_chat_deep_research_sse_emits_untrusted_skill_warning(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("AGENT_DRIVER_PROVIDER", "fake")
    monkeypatch.setenv("CHAT_DEMO_FAKE_SCENARIO", "untrusted_skill_warning")
    monkeypatch.setenv("AGENT_DRIVER_RUNTIME_STORE_KIND", "memory")
    monkeypatch.setenv("CHAT_DEMO_TOOL_PRESET", "web")
    monkeypatch.setenv("CHAT_DEMO_SESSIONS_PATH", str(tmp_path / "sessions.json"))
    monkeypatch.setenv("CHAT_DEMO_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    reset_dependency_caches()
    try:
        events = await _collect_chat_events(message="load the untrusted skill")
    finally:
        reset_dependency_caches()

    skill_event = next(event for event in events if event["event"] == "skill_invoked")
    data = skill_event["data"]
    assert isinstance(data, dict)
    assert data["name"] == "untrusted-research"
    assert data["trusted"] is False
    completed = next(
        event
        for event in events
        if event["event"] == "tool_call_completed"
        and isinstance(event.get("data"), dict)
    )
    tool_rows = completed["data"]["tools"]
    skill_row = next(row for row in tool_rows if row["tool_name"] == "skill_view")
    warnings = skill_row["result_summary"] + str(skill_row)
    assert "untrusted" in warnings.lower()


@pytest.mark.asyncio
async def test_chat_deep_research_sse_compacts_after_skill_invocation(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("AGENT_DRIVER_PROVIDER", "fake")
    monkeypatch.setenv(
        "CHAT_DEMO_FAKE_SCENARIO",
        "compaction_after_skill_invocation",
    )
    monkeypatch.setenv("AGENT_DRIVER_RUNTIME_STORE_KIND", "memory")
    monkeypatch.setenv("CHAT_DEMO_TOOL_PRESET", "web")
    monkeypatch.setenv("CHAT_DEMO_SESSIONS_PATH", str(tmp_path / "sessions.json"))
    monkeypatch.setenv("CHAT_DEMO_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    reset_dependency_caches()
    try:
        events = await _collect_chat_events(message="load a skill then compact")
    finally:
        reset_dependency_caches()

    names = [event["event"] for event in events]
    assert "skill_invoked" in names
    assert "memory_compaction_started" in names
    skill_index = names.index("skill_invoked")
    compaction_indexes = [
        index for index, name in enumerate(names) if name == "memory_compaction_started"
    ]
    assert any(index > skill_index for index in compaction_indexes)
    compacted = next(
        event
        for index, event in enumerate(events)
        if index > skill_index and event["event"] == "memory_compacted"
    )
    data = compacted["data"]
    assert isinstance(data, dict)
    assert data["outcome"] == "successful"


@pytest.mark.asyncio
async def test_chat_deep_research_sse_provider_failure_after_search(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("AGENT_DRIVER_PROVIDER", "fake")
    monkeypatch.setenv("CHAT_DEMO_FAKE_SCENARIO", "provider_failure_after_search")
    monkeypatch.setenv("AGENT_DRIVER_RUNTIME_STORE_KIND", "memory")
    monkeypatch.setenv("CHAT_DEMO_TOOL_PRESET", "web")
    monkeypatch.setenv("CHAT_DEMO_SESSIONS_PATH", str(tmp_path / "sessions.json"))
    monkeypatch.setenv("CHAT_DEMO_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    reset_dependency_caches()
    try:
        events = await _collect_chat_events(message="search then provider fails")
    finally:
        reset_dependency_caches()

    names = [event["event"] for event in events]
    assert "source_ledger_updated" in names
    assert "llm_request_rejected" in names
    assert "run_failed" in names
    assert names.index("source_ledger_updated") < names.index("run_failed")
    failed = next(event for event in events if event["event"] == "run_failed")
    data = failed["data"]
    assert isinstance(data, dict)
    assert data["status_code"] == 429
