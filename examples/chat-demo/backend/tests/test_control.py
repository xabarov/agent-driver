from __future__ import annotations

from agent_driver.contracts import CommandQueueStatus, RuntimeEventType

from app.deps import get_agent_bundle


async def test_control_run_queues_steering_command(client) -> None:
    response = await client.post(
        "/api/chat/runs/run_chat_control/control",
        json={
            "kind": "enqueue_user_message",
            "priority": "next",
            "payload": {"message": "tighten the answer"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["queue_id"]

    bundle = get_agent_bundle()
    queued = bundle.command_queue_store.get(payload["queue_id"])
    events = bundle.event_log.list_for_run("run_chat_control")
    assert queued is not None
    assert queued.status == CommandQueueStatus.QUEUED
    assert any(event.type == RuntimeEventType.CONTROL_REQUESTED for event in events)
    assert any(event.type == RuntimeEventType.COMMAND_QUEUED for event in events)


async def test_control_run_persists_steering_history(client) -> None:
    session_id: str | None = None
    run_id: str | None = None
    async with client.stream(
        "POST",
        "/api/chat/messages",
        json={"message": "hello steering history"},
    ) as stream_response:
        assert stream_response.status_code == 200
        session_id = stream_response.headers.get("x-session-id")
        run_id = stream_response.headers.get("x-run-id")
        async for line in stream_response.aiter_lines():
            if line == "event: run_completed":
                break

    assert session_id is not None
    assert run_id is not None
    queued_response = await client.post(
        f"/api/chat/runs/{run_id}/control",
        json={
            "kind": "enqueue_user_message",
            "priority": "next",
            "payload": {"message": "persist this steering"},
        },
    )
    assert queued_response.status_code == 200
    queue_id = queued_response.json()["queue_id"]

    detail = await client.get(f"/api/sessions/{session_id}")
    assert detail.status_code == 200
    metadata = detail.json()["metadata_by_run"][run_id]
    controls = metadata["steering_controls"]
    assert controls[0]["queue_id"] == queue_id
    assert controls[0]["kind"] == "enqueue_user_message"
    assert controls[0]["status"] == "queued"
    assert controls[0]["payload"]["message"] == "persist this steering"

    cancelled = await client.delete(f"/api/chat/commands/{queue_id}")
    assert cancelled.status_code == 200
    detail_after_cancel = await client.get(f"/api/sessions/{session_id}")
    controls_after_cancel = detail_after_cancel.json()["metadata_by_run"][run_id][
        "steering_controls"
    ]
    assert controls_after_cancel[0]["queue_id"] == queue_id
    assert controls_after_cancel[0]["status"] == "cancelled"


async def test_cancel_queued_command_marks_command_cancelled(client) -> None:
    queued_response = await client.post(
        "/api/chat/runs/run_chat_cancel_control/control",
        json={
            "kind": "enqueue_user_message",
            "payload": {"message": "cancel this"},
        },
    )
    queue_id = queued_response.json()["queue_id"]

    response = await client.delete(f"/api/chat/commands/{queue_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    bundle = get_agent_bundle()
    queued = bundle.command_queue_store.get(queue_id)
    events = bundle.event_log.list_for_run("run_chat_cancel_control")
    assert queued is not None
    assert queued.status == CommandQueueStatus.CANCELLED
    assert any(event.type == RuntimeEventType.COMMAND_CANCELLED for event in events)
