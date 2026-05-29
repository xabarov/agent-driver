from __future__ import annotations


async def test_chat_persists_transcript(client) -> None:
    session_id = "session_testpersist"
    async with client.stream(
        "POST",
        "/api/chat/messages",
        json={"session_id": session_id, "message": "store this"},
    ) as response:
        assert response.status_code == 200
        async for line in response.aiter_lines():
            if line == "event: run_completed":
                break

    session_response = await client.get(f"/api/sessions/{session_id}")
    assert session_response.status_code == 200
    transcript = session_response.json()["transcript"]
    roles = [item["role"] for item in transcript]
    assert "user" in roles
    assert "assistant" in roles

