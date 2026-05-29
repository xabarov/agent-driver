"""Replay helpers derived from persisted runtime event/checkpoint stores."""

from __future__ import annotations

from typing import Any

from agent_driver.runtime.storage import CheckpointStore, RuntimeEventLog


def _event_trajectory(events: list[dict[str, Any]]) -> list[str]:
    return [str(event.get("type", "")) for event in events]


def replay_from_persisted(
    *,
    run_id: str,
    event_log: RuntimeEventLog,
    checkpoint_store: CheckpointStore,
) -> dict[str, Any]:
    """Build replay/debug payload from persisted event and checkpoint stores."""
    events = [item.model_dump(mode="json") for item in event_log.list_for_run(run_id)]
    latest = checkpoint_store.latest(run_id)
    checkpoints = [
        row.ref.model_dump(mode="json")
        for row in checkpoint_store.list_checkpoints(run_id)
    ]
    metadata = latest.state.metadata if latest is not None else {}
    return {
        "run_id": run_id,
        "event_count": len(events),
        "events": events,
        "trajectory": _event_trajectory(events),
        "latest_checkpoint": latest.ref.model_dump(mode="json") if latest else None,
        "checkpoints": checkpoints,
        "metadata": metadata,
    }


def graph_profile_tool_summary(persisted_replay: dict[str, Any]) -> dict[str, Any]:
    """Render compact graph/profile/tool summary for devtools."""
    metadata = persisted_replay.get("metadata", {})
    events = persisted_replay.get("events", [])
    tool_statuses: list[str] = []
    for event in events:
        payload = event.get("payload")
        if isinstance(payload, dict) and isinstance(payload.get("statuses"), list):
            for status in payload["statuses"]:
                if isinstance(status, str):
                    tool_statuses.append(status)
    return {
        "run_id": persisted_replay.get("run_id"),
        "graph_id": metadata.get("graph_id"),
        "agent_profile": metadata.get("agent_profile"),
        "trajectory": persisted_replay.get("trajectory", []),
        "tool_statuses": tool_statuses,
    }
