from __future__ import annotations


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
