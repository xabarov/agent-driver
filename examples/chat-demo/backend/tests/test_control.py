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
