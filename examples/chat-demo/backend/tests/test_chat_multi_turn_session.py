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
