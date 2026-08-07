"""SQLite command queue store for steering control-plane."""

from __future__ import annotations

from typing import Any

from agent_driver.contracts.control import (
    CommandQueueItem,
    CommandQueueStatus,
    ControlRequest,
    LiveMessagePhase,
    LiveRunState,
    dispatch_order,
)
from agent_driver.persistence import SqliteStoreBase
from agent_driver.runtime.control.in_memory import InMemoryCommandQueueStore


class SqliteCommandQueueStore(SqliteStoreBase):
    """SQLite-backed command queue store."""

    def __deepcopy__(self, memo: dict) -> "SqliteCommandQueueStore":
        """Return self — the store wraps a shared SQLite connection.

        ``create_agent`` deep-copies the ``RunnerConfig``; a live
        ``sqlite3.Connection`` is not copyable, and two independent copies of a
        shared command queue would defeat its purpose (the runner must read the
        same queue the host writes to). Identity-copy keeps the shared store.
        """
        memo[id(self)] = self
        return self

    def _init_schema(self) -> None:
        self._execute("""
            CREATE TABLE IF NOT EXISTS command_queue (
                queue_id TEXT PRIMARY KEY,
                control_id TEXT NOT NULL,
                run_id TEXT,
                thread_id TEXT,
                agent_id TEXT,
                priority TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                source TEXT NOT NULL,
                dedupe_key TEXT,
                created_at TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """)
        self._execute("""
            CREATE TABLE IF NOT EXISTS live_message_runs (
                run_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            )
            """)
        self._execute("""
            CREATE TABLE IF NOT EXISTS control_schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """)
        self._execute(
            "INSERT OR REPLACE INTO control_schema_meta (key, value) VALUES (?, ?)",
            ("live_message_contract_version", "1"),
        )

    def enqueue(self, request: ControlRequest) -> CommandQueueItem:
        """Persist a new queued command or return a deduped pending one."""
        return self._mutate("enqueue", request)

    def admit(
        self,
        request: ControlRequest,
        *,
        accepted_phase: LiveMessagePhase | None = None,
    ) -> CommandQueueItem:
        return self._mutate("admit", request, accepted_phase=accepted_phase)

    def get(self, queue_id: str) -> CommandQueueItem | None:
        """Return one command by id."""
        rows = self._query(
            "SELECT payload FROM command_queue WHERE queue_id = ?",
            (queue_id,),
        )
        if not rows:
            return None
        return CommandQueueItem.model_validate_json(rows[0][0])

    def list_pending(
        self,
        *,
        run_id: str | None = None,
        thread_id: str | None = None,
        agent_id: str | None = None,
    ) -> list[CommandQueueItem]:
        """Return queued commands ordered by priority and insertion order."""
        rows = self._query(
            """
            SELECT payload FROM command_queue
            WHERE status = ?
            ORDER BY created_at ASC, queue_id ASC
            """,
            (CommandQueueStatus.QUEUED.value,),
        )
        items = [
            item
            for (payload,) in rows
            if _matches_route(
                item := CommandQueueItem.model_validate_json(payload),
                run_id=run_id,
                thread_id=thread_id,
                agent_id=agent_id,
            )
        ]
        items.sort(
            key=lambda item: (dispatch_order(item), item.sequence, item.created_at)
        )
        return items

    def list_for_run(self, run_id: str) -> list[CommandQueueItem]:
        rows = self._query(
            "SELECT payload FROM command_queue WHERE run_id = ? "
            "ORDER BY created_at ASC, queue_id ASC",
            (run_id,),
        )
        items = [CommandQueueItem.model_validate_json(payload) for (payload,) in rows]
        items.sort(key=lambda item: (item.sequence or 2**63, item.created_at))
        return items

    def dequeue_next(
        self,
        *,
        run_id: str | None = None,
        thread_id: str | None = None,
        agent_id: str | None = None,
    ) -> CommandQueueItem | None:
        """Return the next queued command without marking it applied."""
        pending = self.list_pending(
            run_id=run_id,
            thread_id=thread_id,
            agent_id=agent_id,
        )
        return pending[0] if pending else None

    def cancel(self, queue_id: str) -> CommandQueueItem | None:
        """Mark a queued command as cancelled."""
        return self._mutate("cancel", queue_id)

    def mark_applied(
        self,
        queue_id: str,
        *,
        claimant_id: str | None = None,
        applied_phase: LiveMessagePhase | None = None,
    ) -> CommandQueueItem | None:
        """Mark a queued command as applied."""
        return self._mutate(
            "mark_applied",
            queue_id,
            claimant_id=claimant_id,
            applied_phase=applied_phase,
        )

    def mark_failed(self, queue_id: str, *, error: str) -> CommandQueueItem | None:
        """Mark a queued command as failed."""
        return self._mutate("mark_failed", queue_id, error=error)

    def set_run_phase(
        self,
        run_id: str,
        phase: LiveMessagePhase,
        *,
        thread_id: str | None = None,
        agent_id: str | None = None,
    ) -> LiveRunState:
        return self._mutate(
            "set_run_phase",
            run_id,
            phase,
            thread_id=thread_id,
            agent_id=agent_id,
        )

    def get_run_state(self, run_id: str) -> LiveRunState | None:
        rows = self._query(
            "SELECT payload FROM live_message_runs WHERE run_id = ?", (run_id,)
        )
        return LiveRunState.model_validate_json(rows[0][0]) if rows else None

    def current_llm_generation(self, run_id: str) -> int:
        state = self.get_run_state(run_id)
        return state.llm_generation if state is not None else 0

    def claim_for_boundary(
        self,
        *,
        run_id: str,
        claimant_id: str,
        applied_phase: LiveMessagePhase,
    ) -> CommandQueueItem | None:
        return self._mutate(
            "claim_for_boundary",
            run_id=run_id,
            claimant_id=claimant_id,
            applied_phase=applied_phase,
        )

    def claim_hard_redirect(
        self, *, run_id: str, claimant_id: str, expected_generation: int
    ) -> CommandQueueItem | None:
        return self._mutate(
            "claim_hard_redirect",
            run_id=run_id,
            claimant_id=claimant_id,
            expected_generation=expected_generation,
        )

    def release_claim(
        self, queue_id: str, *, claimant_id: str
    ) -> CommandQueueItem | None:
        return self._mutate("release_claim", queue_id, claimant_id=claimant_id)

    def commit_terminal(
        self, run_id: str, *, stopped: bool = False
    ) -> list[CommandQueueItem]:
        return self._mutate("commit_terminal", run_id, stopped=stopped)

    def stop_run(self, run_id: str) -> list[CommandQueueItem]:
        return self._mutate("stop_run", run_id)

    def cancel_next(self, queue_id: str) -> CommandQueueItem | None:
        return self._mutate("cancel_next", queue_id)

    def claim_next_handoff(
        self, *, source_run_id: str, claimant_id: str
    ) -> CommandQueueItem | None:
        return self._mutate(
            "claim_next_handoff",
            source_run_id=source_run_id,
            claimant_id=claimant_id,
        )

    def complete_handoff(
        self,
        queue_id: str,
        *,
        claimant_id: str,
        destination_turn_id: str,
    ) -> CommandQueueItem | None:
        return self._mutate(
            "complete_handoff",
            queue_id,
            claimant_id=claimant_id,
            destination_turn_id=destination_turn_id,
        )

    def contract_schema_version(self) -> int:
        rows = self._query(
            "SELECT value FROM control_schema_meta WHERE key = ?",
            ("live_message_contract_version",),
        )
        return int(rows[0][0]) if rows else 0

    def quarantine_legacy_rows(self) -> list[CommandQueueItem]:
        return self._mutate("quarantine_legacy_rows")

    def _mutate(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Apply the reference state machine under SQLite's process lock."""
        with self._lock:
            memory = self._snapshot_unlocked()
            quarantined = memory.quarantine_legacy_rows()
            result = (
                quarantined
                if method == "quarantine_legacy_rows"
                else getattr(memory, method)(*args, **kwargs)
            )
            self._persist_snapshot_unlocked(memory)
            return result

    def _snapshot_unlocked(self) -> InMemoryCommandQueueStore:
        memory = InMemoryCommandQueueStore()
        rows = self._conn.execute(
            "SELECT payload FROM command_queue ORDER BY created_at, queue_id"
        ).fetchall()
        items = [CommandQueueItem.model_validate_json(row[0]) for row in rows]
        items.sort(key=lambda item: (item.sequence or 2**63, item.created_at))
        memory._items = {item.queue_id: item for item in items}  # noqa: SLF001
        memory._order = [item.queue_id for item in items]  # noqa: SLF001
        state_rows = self._conn.execute(
            "SELECT payload FROM live_message_runs"
        ).fetchall()
        states = [LiveRunState.model_validate_json(row[0]) for row in state_rows]
        memory._run_states = {state.run_id: state for state in states}  # noqa: SLF001
        return memory

    def _persist_snapshot_unlocked(self, memory: InMemoryCommandQueueStore) -> None:
        for item in memory._items.values():  # noqa: SLF001
            self._conn.execute(
                """
                INSERT OR REPLACE INTO command_queue (
                    queue_id, control_id, run_id, thread_id, agent_id, priority,
                    kind, status, source, dedupe_key, created_at, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.queue_id,
                    item.control_id,
                    item.run_id,
                    item.thread_id,
                    item.agent_id,
                    item.priority.value,
                    item.kind.value,
                    item.status.value,
                    item.source,
                    item.dedupe_key,
                    item.created_at,
                    item.model_dump_json(),
                ),
            )
        for state in memory._run_states.values():  # noqa: SLF001
            self._conn.execute(
                "INSERT OR REPLACE INTO live_message_runs (run_id, payload) VALUES (?, ?)",
                (state.run_id, state.model_dump_json()),
            )
        self._conn.commit()


def _matches_route(
    item: CommandQueueItem,
    *,
    run_id: str | None,
    thread_id: str | None,
    agent_id: str | None,
) -> bool:
    if run_id is not None and item.run_id != run_id:
        return False
    if thread_id is not None and item.thread_id != thread_id:
        return False
    if agent_id is not None and item.agent_id != agent_id:
        return False
    return True


__all__ = ["SqliteCommandQueueStore"]
