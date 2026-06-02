from __future__ import annotations

import json

import pytest
from app.api.chat import _deep_research_source_counts
from app.deps import reset_dependency_caches
from app.main import create_app
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


async def test_deep_research_source_counts_accepts_domain_list() -> None:
    counts = _deep_research_source_counts(
        metadata={},
        trace_summary={
            "research": {"unique_domains": ["example.com", "example.org"]},
            "research_efficiency": {"verified_read_count": 1},
        },
    )

    assert counts.distinct_domains == 2
    assert counts.verified == 1


async def test_session_replay_returns_events(client) -> None:
    session_id: str | None = None
    run_id: str | None = None
    async with client.stream(
        "POST",
        "/api/chat/messages",
        json={"message": "hello replay"},
    ) as response:
        assert response.status_code == 200
        session_id = response.headers.get("x-session-id")
        run_id = response.headers.get("x-run-id")
        async for line in response.aiter_lines():
            if line == "event: run_completed":
                break

    assert session_id is not None
    assert run_id is not None
    replay = await client.get(
        f"/api/sessions/{session_id}/replay",
        params={"run_id": run_id},
    )
    assert replay.status_code == 200
    payload = replay.json()
    assert payload["run_id"] == run_id
    event_names = {item["event"] for item in payload["events"]}
    assert "run_started" in event_names
    assert "run_completed" in event_names


async def test_session_replay_includes_steering_events(client) -> None:
    session_id: str | None = None
    run_id: str | None = None
    async with client.stream(
        "POST",
        "/api/chat/messages",
        json={"message": "hello steering replay"},
    ) as response:
        assert response.status_code == 200
        session_id = response.headers.get("x-session-id")
        run_id = response.headers.get("x-run-id")
        async for line in response.aiter_lines():
            if line == "event: run_completed":
                break

    assert session_id is not None
    assert run_id is not None
    queued = await client.post(
        f"/api/chat/runs/{run_id}/control",
        json={
            "kind": "enqueue_user_message",
            "payload": {"message": "record this steering event"},
        },
    )
    assert queued.status_code == 200

    replay = await client.get(
        f"/api/sessions/{session_id}/replay",
        params={"run_id": run_id},
    )

    assert replay.status_code == 200
    payload = replay.json()
    event_names = [item["event"] for item in payload["events"]]
    assert "control_requested" in event_names
    assert "command_queued" in event_names


async def test_run_trace_summary_returns_scenario_verdict(client) -> None:
    run_id: str | None = None
    async with client.stream(
        "POST",
        "/api/chat/messages",
        json={"message": "hello trace summary"},
    ) as response:
        assert response.status_code == 200
        run_id = response.headers.get("x-run-id")
        async for line in response.aiter_lines():
            if line == "event: run_completed":
                break

    assert run_id is not None
    summary = await client.get(f"/api/chat/runs/{run_id}/trace-summary")

    assert summary.status_code == 200
    payload = summary.json()
    assert payload["run_id"] == run_id
    assert payload["terminal_event"] == "run_completed"
    assert payload["verdict"] in {"pass", "fail"}
    assert "failures" in payload


async def test_deep_research_state_returns_run_projection(client) -> None:
    run_id: str | None = None
    async with client.stream(
        "POST",
        "/api/chat/messages",
        json={
            "message": "prepare a deep research report",
            "research_mode": "deep",
            "research_profile": "hard",
            "profile_source": "user_selected",
        },
    ) as response:
        assert response.status_code == 200
        run_id = response.headers.get("x-run-id")
        async for line in response.aiter_lines():
            if line == "event: run_completed":
                break

    assert run_id is not None
    response = await client.get(f"/api/chat/runs/{run_id}/deep-research-state")

    assert response.status_code == 200
    payload = response.json()
    assert payload["runId"] == run_id
    assert payload["researchMode"] == "deep"
    assert payload["profile"] == "hard"
    assert payload["profileSource"] == "user_selected"
    assert payload["trace"]["runId"] == run_id
    assert "metrics" in payload
    assert "artifacts" in payload
    assert "sources" in payload


async def test_fake_compaction_notice_scenario_emits_lifecycle(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("AGENT_DRIVER_PROVIDER", "fake")
    monkeypatch.setenv("CHAT_DEMO_FAKE_SCENARIO", "compaction_notice")
    monkeypatch.setenv("AGENT_DRIVER_RUNTIME_STORE_KIND", "memory")
    monkeypatch.setenv("CHAT_DEMO_TOOL_PRESET", "web")
    monkeypatch.setenv("CHAT_DEMO_SESSIONS_PATH", str(tmp_path / "sessions.json"))
    reset_dependency_caches()
    application = create_app()

    run_id: str | None = None
    events: list[str] = []
    payloads: list[dict[str, object]] = []
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        async with http.stream(
            "POST",
            "/api/chat/messages",
            json={"message": "trigger synthetic compaction"},
        ) as response:
            assert response.status_code == 200
            run_id = response.headers.get("x-run-id")
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    event = line.removeprefix("event: ").strip()
                    events.append(event)
                    if event == "run_completed":
                        break
                if line.startswith("data: "):
                    payloads.append(json.loads(line.removeprefix("data: ")))

        assert run_id is not None
        summary = await http.get(f"/api/chat/runs/{run_id}/trace-summary")

    assert "memory_compaction_started" in events
    assert "memory_compacted" in events
    assert any(
        item.get("event") == "memory_compacted"
        and item.get("data", {}).get("outcome") == "successful"
        for item in payloads
    )
    assert summary.status_code == 200
    assert summary.json()["compaction"]["successful"] == 1
    reset_dependency_caches()
