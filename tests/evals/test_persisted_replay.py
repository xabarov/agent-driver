"""Persisted replay helper tests."""

from __future__ import annotations

from agent_driver.contracts import AgentRunInput, RuntimeEventType, new_runtime_event
from agent_driver.evals import graph_profile_tool_summary, replay_from_persisted
from agent_driver.runtime import InMemoryCheckpointStore, InMemoryEventLog
from agent_driver.runtime.state import RuntimeState


def test_replay_from_persisted_builds_replay_payload() -> None:
    """Persisted helper should reconstruct event/checkpoint replay payload."""
    event_log = InMemoryEventLog()
    checkpoint_store = InMemoryCheckpointStore()
    run_id = "run_persisted_1"
    state = RuntimeState(
        run_input=AgentRunInput(
            input="hello",
            run_id=run_id,
            agent_id="agent",
            graph_preset="single_react",
        ),
        metadata={"graph_id": "single_agent_runtime", "agent_profile": "react_text"},
    )
    checkpoint_store.save(
        graph_id="single_agent_runtime", node_id="run_started", state=state
    )
    event_log.append(
        new_runtime_event(
            event_type=RuntimeEventType.RUN_STARTED,
            context={"run_id": run_id, "attempt_id": "attempt_1", "seq": 1},
        )
    )
    event_log.append(
        new_runtime_event(
            event_type=RuntimeEventType.TOOL_CALL_COMPLETED,
            context={"run_id": run_id, "attempt_id": "attempt_1", "seq": 2},
            options={"payload": {"statuses": ["completed"]}},
        )
    )
    replay = replay_from_persisted(
        run_id=run_id, event_log=event_log, checkpoint_store=checkpoint_store
    )
    assert replay["event_count"] == 2
    assert replay["latest_checkpoint"] is not None
    summary = graph_profile_tool_summary(replay)
    assert summary["graph_id"] == "single_agent_runtime"
    assert summary["tool_statuses"] == ["completed"]
