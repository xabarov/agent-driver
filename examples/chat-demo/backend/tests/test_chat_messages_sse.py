from __future__ import annotations


async def test_chat_messages_sse(client) -> None:
    events: list[str] = []
    async with client.stream(
        "POST",
        "/api/chat/messages",
        json={"message": "hello"},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                events.append(line.removeprefix("event: ").strip())
            if line == "event: run_completed":
                break
    assert "run_started" in events
    assert "run_completed" in events

