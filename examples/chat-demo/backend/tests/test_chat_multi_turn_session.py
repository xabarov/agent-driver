"""Two-turn chat session: stream events and persisted transcript."""

from __future__ import annotations


async def test_chat_two_turn_session_persists_both_user_messages(client) -> None:
    session_id = "session_multi_turn_e2e"
    for message in ("turn one hello", "turn two follow-up"):
        async with client.stream(
            "POST",
            "/api/chat/messages",
            json={"session_id": session_id, "message": message},
        ) as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if line == "event: run_completed":
                    break

    session_response = await client.get(f"/api/sessions/{session_id}")
    assert session_response.status_code == 200
    transcript = session_response.json()["transcript"]
    user_contents = [
        item["content"]
        for item in transcript
        if item.get("role") == "user"
    ]
    assert "turn one hello" in user_contents
    assert "turn two follow-up" in user_contents


async def test_chat_retry_truncates_context_from_retried_run(client) -> None:
    session_id = "session_retry_truncates"
    for message in ("turn one hello", "turn two follow-up"):
        async with client.stream(
            "POST",
            "/api/chat/messages",
            json={"session_id": session_id, "message": message},
        ) as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if line == "event: run_completed":
                    break

    before_response = await client.get(f"/api/sessions/{session_id}")
    assert before_response.status_code == 200
    first_run_id = before_response.json()["run_ids"][0]

    async with client.stream(
        "POST",
        "/api/chat/messages",
        json={
            "session_id": session_id,
            "message": "turn one hello",
            "retry_from_run_id": first_run_id,
        },
    ) as response:
        assert response.status_code == 200
        async for line in response.aiter_lines():
            if line == "event: run_completed":
                break

    session_response = await client.get(f"/api/sessions/{session_id}")
    assert session_response.status_code == 200
    session = session_response.json()
    user_contents = [
        item["content"]
        for item in session["transcript"]
        if item.get("role") == "user"
    ]
    assert user_contents == ["turn one hello"]
    assert len(session["run_ids"]) == 1
    assert session["run_ids"][0] != first_run_id


async def test_chat_duplicate_client_request_replays_existing_run(client) -> None:
    session_id = "session_idempotent_request"
    request_id = "client-request-1"

    async with client.stream(
        "POST",
        "/api/chat/messages",
        json={
            "session_id": session_id,
            "message": "idempotent hello",
            "client_request_id": request_id,
        },
    ) as response:
        assert response.status_code == 200
        first_run_id = response.headers["x-run-id"]
        async for line in response.aiter_lines():
            if line == "event: run_completed":
                break

    async with client.stream(
        "POST",
        "/api/chat/messages",
        json={
            "session_id": session_id,
            "message": "idempotent hello",
            "client_request_id": request_id,
        },
    ) as response:
        assert response.status_code == 200
        assert response.headers["x-run-id"] == first_run_id
        async for line in response.aiter_lines():
            if line == "event: run_completed":
                break

    session_response = await client.get(f"/api/sessions/{session_id}")
    assert session_response.status_code == 200
    session = session_response.json()
    assert session["run_ids"] == [first_run_id]
    user_contents = [
        item["content"]
        for item in session["transcript"]
        if item.get("role") == "user"
    ]
    assert user_contents == ["idempotent hello"]


async def test_chat_reconnect_continues_after_first_stream_closes(client) -> None:
    session_id = "session_reconnect_background"
    request_id = "client-request-reconnect"

    async with client.stream(
        "POST",
        "/api/chat/messages",
        json={
            "session_id": session_id,
            "message": "reconnect hello",
            "client_request_id": request_id,
        },
    ) as response:
        assert response.status_code == 200
        run_id = response.headers["x-run-id"]
        async for line in response.aiter_lines():
            if line == "event: run_started":
                break

    async with client.stream(
        "POST",
        "/api/chat/messages",
        json={
            "session_id": session_id,
            "message": "reconnect hello",
            "client_request_id": request_id,
        },
    ) as response:
        assert response.status_code == 200
        assert response.headers["x-run-id"] == run_id
        seen_completed = False
        async for line in response.aiter_lines():
            if line == "event: run_completed":
                seen_completed = True
                break
        assert seen_completed

    session_response = await client.get(f"/api/sessions/{session_id}")
    session = session_response.json()
    assert session["run_ids"] == [run_id]
    assert [item["role"] for item in session["transcript"]].count("user") == 1
    assert [item["role"] for item in session["transcript"]].count("assistant") == 1


async def test_retry_from_run_id_overrides_prior_client_request_mapping(client) -> None:
    session_id = "session_retry_same_request_id"
    request_id = "client-request-retry"

    async with client.stream(
        "POST",
        "/api/chat/messages",
        json={
            "session_id": session_id,
            "message": "retry request",
            "client_request_id": request_id,
        },
    ) as response:
        assert response.status_code == 200
        first_run_id = response.headers["x-run-id"]
        async for line in response.aiter_lines():
            if line == "event: run_completed":
                break

    async with client.stream(
        "POST",
        "/api/chat/messages",
        json={
            "session_id": session_id,
            "message": "retry request",
            "client_request_id": request_id,
            "retry_from_run_id": first_run_id,
        },
    ) as response:
        assert response.status_code == 200
        replacement_run_id = response.headers["x-run-id"]
        async for line in response.aiter_lines():
            if line == "event: run_completed":
                break

    assert replacement_run_id != first_run_id
    session_response = await client.get(f"/api/sessions/{session_id}")
    session = session_response.json()
    assert session["run_ids"] == [replacement_run_id]
    assert [item["role"] for item in session["transcript"]].count("user") == 1
