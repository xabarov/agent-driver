"""PostgreSQL-backed durable control plane (R2 — epic 058).

Production-durable implementations of all four generic control-plane stores,
mirroring the SQLite ones one-for-one but coordinated through a real Postgres
cluster (the transactional/unique/CAS semantics a multi-worker product relies on
that a single-file SQLite backend does not prove):

* :class:`PostgresApprovalConsumptionStore` — exactly-once approval consumption
  (``INSERT … ON CONFLICT DO NOTHING`` is the cross-process compare-and-swap).
* :class:`PostgresAbortLifecycleStore` — durable ``requested → observed →
  cancelled | completed_before_cancel`` lifecycle, restart-queryable.
* :class:`PostgresPlanArtifactStore` — durable approved-plan artifacts.
* :class:`PostgresCommandQueueStore` — durable cross-process steering queue.

All four live in one **generic** schema (default ``agent_driver_control``) with
their own DDL; nothing here shares a transaction with product tables and nothing
carries product (e.g. PentestLens) semantics. psycopg (v3) is imported lazily so
the dependency is only required when a Postgres store is actually constructed
(``pip install 'agent-driver[postgres]'``).
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any

from agent_driver.contracts.control import (
    CommandQueueItem,
    CommandQueueStatus,
    ControlPriority,
    ControlRequest,
    LiveMessagePhase,
    LiveMessageSemantic,
    LiveRunState,
)
from agent_driver.runtime.control.abort_store import (
    AbortLifecycleState,
    AbortRecord,
)
from agent_driver.runtime.control.approval_store import (
    ApprovalConsumeRequest,
    ConsumeOutcome,
    ConsumeStatus,
)
from agent_driver.contracts.context import PlanArtifact
from agent_driver.runtime.control.in_memory import InMemoryCommandQueueStore

_PRIORITY_ORDER = {
    ControlPriority.NOW: 0,
    ControlPriority.NEXT: 1,
    ControlPriority.LATER: 2,
}


def _pg_dependencies() -> tuple[Any, Any]:
    """Import psycopg dependencies lazily for the optional ``postgres`` extra."""
    try:
        psycopg_module = import_module("psycopg")
        rows_module = import_module("psycopg.rows")
        connect = psycopg_module.connect
        dict_row = rows_module.dict_row
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "PostgreSQL support requires optional dependency: "
            "pip install 'agent-driver[postgres]'"
        ) from exc
    return connect, dict_row


@dataclass(frozen=True)
class PostgresControlStoreConfig:
    """Configuration shared by the four Postgres control-plane stores."""

    dsn: str
    schema: str = "agent_driver_control"
    auto_create_schema: bool = True
    connect_timeout_seconds: int = 5
    application_name: str = "agent_driver_control"


class _PostgresControlStoreBase:
    """Shared connection/DDL plumbing for the control-plane Postgres stores."""

    #: Subclass sets this to its unqualified table name.
    _table_name: str = ""

    def __init__(self, *, config: PostgresControlStoreConfig) -> None:
        self._config = config
        self._table = f"{config.schema}.{self._table_name}"
        if config.auto_create_schema:
            self.ensure_schema()

    def _connect_kwargs(self) -> dict[str, Any]:
        return {
            "connect_timeout": self._config.connect_timeout_seconds,
            "application_name": self._config.application_name,
        }

    def _connect(self, *, autocommit: bool) -> Any:
        connect, dict_row = _pg_dependencies()
        return connect(
            self._config.dsn,
            autocommit=autocommit,
            row_factory=dict_row,
            **self._connect_kwargs(),
        )

    def _ensure_control_schema(self, cur: Any) -> None:
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {self._config.schema}")

    def ensure_schema(self) -> None:
        """Create the schema and this store's table (idempotent)."""
        with self._connect(autocommit=True) as conn:
            with conn.cursor() as cur:
                self._ensure_control_schema(cur)
                self._init_schema(cur)

    def _init_schema(self, cur: Any) -> None:  # pragma: no cover - overridden
        raise NotImplementedError


class PostgresApprovalConsumptionStore(_PostgresControlStoreBase):
    """Postgres CAS ledger — exactly-once approval consumption, crash-safe.

    ``INSERT … ON CONFLICT DO NOTHING RETURNING`` is the cross-process swap: the
    row is written BEFORE the tool runs, so exactly one caller is told
    ``CONSUMED`` (may drive the tool); every concurrent or later duplicate reads
    the committed row back and is told ``DUPLICATE`` (replay) or ``CONFLICT`` (a
    different decision already consumed this interrupt). Untargeted
    ``ON CONFLICT`` covers both the primary key and the partial idempotency-key
    unique index, mirroring SQLite's ``INSERT OR IGNORE``.
    """

    _table_name = "approval_consumptions"

    def _init_schema(self, cur: Any) -> None:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._table} (
                run_id TEXT NOT NULL,
                interrupt_id TEXT NOT NULL,
                idempotency_key TEXT,
                decision TEXT NOT NULL,
                result_ref TEXT,
                result_payload TEXT,
                PRIMARY KEY (run_id, interrupt_id)
            )
            """
        )
        cur.execute(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS approval_consumptions_key_idx
            ON {self._table} (run_id, idempotency_key)
            WHERE idempotency_key IS NOT NULL
            """
        )

    def try_consume(self, request: ApprovalConsumeRequest) -> ConsumeOutcome:
        with self._connect(autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self._table}
                        (run_id, interrupt_id, idempotency_key, decision)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    RETURNING run_id
                    """,
                    (
                        request.run_id,
                        request.interrupt_id,
                        request.idempotency_key,
                        request.decision,
                    ),
                )
                if cur.fetchone() is not None:
                    return ConsumeOutcome(ConsumeStatus.CONSUMED)
                # Conflict on the primary key or the idempotency-key index — read
                # the committed winner back to classify.
                cur.execute(
                    f"""
                    SELECT decision, result_ref, result_payload FROM {self._table}
                    WHERE run_id = %s AND interrupt_id = %s
                    """,
                    (request.run_id, request.interrupt_id),
                )
                row = cur.fetchone()
                if row is None and request.idempotency_key is not None:
                    cur.execute(
                        f"""
                        SELECT decision, result_ref, result_payload FROM {self._table}
                        WHERE run_id = %s AND idempotency_key = %s
                        """,
                        (request.run_id, request.idempotency_key),
                    )
                    row = cur.fetchone()
        if row is None:  # pragma: no cover - lost-then-vanished race edge
            return ConsumeOutcome(
                ConsumeStatus.CONFLICT, detail="consume ignored but no row found"
            )
        if row["decision"] != request.decision:
            return ConsumeOutcome(
                ConsumeStatus.CONFLICT,
                prior_decision=row["decision"],
                detail="approval already consumed with a different decision",
            )
        return ConsumeOutcome(
            ConsumeStatus.DUPLICATE,
            prior_decision=row["decision"],
            prior_result_ref=row["result_ref"],
            prior_result_payload=row["result_payload"],
        )

    def record_result(
        self,
        *,
        run_id: str,
        interrupt_id: str,
        result_ref: str | None = None,
        result_payload: str | None = None,
    ) -> None:
        with self._connect(autocommit=True) as conn:
            with conn.cursor() as cur:
                if result_ref is not None:
                    cur.execute(
                        f"UPDATE {self._table} SET result_ref = %s "
                        "WHERE run_id = %s AND interrupt_id = %s",
                        (result_ref, run_id, interrupt_id),
                    )
                if result_payload is not None:
                    cur.execute(
                        f"UPDATE {self._table} SET result_payload = %s "
                        "WHERE run_id = %s AND interrupt_id = %s",
                        (result_payload, run_id, interrupt_id),
                    )

    def get(self, *, run_id: str, interrupt_id: str) -> ConsumeOutcome | None:
        with self._connect(autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT decision, result_ref, result_payload FROM {self._table}
                    WHERE run_id = %s AND interrupt_id = %s
                    """,
                    (run_id, interrupt_id),
                )
                row = cur.fetchone()
        if row is None:
            return None
        return ConsumeOutcome(
            ConsumeStatus.DUPLICATE,
            prior_decision=row["decision"],
            prior_result_ref=row["result_ref"],
            prior_result_payload=row["result_payload"],
        )


class PostgresAbortLifecycleStore(_PostgresControlStoreBase):
    """Postgres abort lifecycle ledger — durable + restart-queryable."""

    _table_name = "abort_lifecycle"

    def _init_schema(self, cur: Any) -> None:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._table} (
                run_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                observed BOOLEAN NOT NULL DEFAULT FALSE,
                reason TEXT,
                actor TEXT
            )
            """
        )

    @staticmethod
    def _row_to_record(row: dict[str, Any]) -> AbortRecord:
        return AbortRecord(
            run_id=row["run_id"],
            state=AbortLifecycleState(row["state"]),
            observed=bool(row["observed"]),
            reason=row["reason"],
            actor=row["actor"],
        )

    def get(self, run_id: str) -> AbortRecord | None:
        with self._connect(autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT * FROM {self._table} WHERE run_id = %s", (run_id,)
                )
                row = cur.fetchone()
        return self._row_to_record(row) if row is not None else None

    def request_abort(
        self, run_id: str, *, reason: str | None = None, actor: str | None = None
    ) -> AbortRecord:
        with self._connect(autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self._table} (run_id, state, observed, reason, actor)
                    VALUES (%s, %s, FALSE, %s, %s)
                    ON CONFLICT (run_id) DO NOTHING
                    """,
                    (run_id, AbortLifecycleState.REQUESTED.value, reason, actor),
                )
        got = self.get(run_id)
        assert got is not None  # just inserted or already present
        return got

    def mark_observed(
        self, run_id: str, *, reason: str | None = None, actor: str | None = None
    ) -> AbortRecord:
        with self._connect(autocommit=True) as conn:
            with conn.cursor() as cur:
                # Create-or-advance, but never move a terminal row backwards.
                cur.execute(
                    f"""
                    INSERT INTO {self._table} (run_id, state, observed, reason, actor)
                    VALUES (%s, %s, TRUE, %s, %s)
                    ON CONFLICT (run_id) DO UPDATE SET
                        state = CASE
                            WHEN {self._table}.state IN (%s, %s)
                                THEN {self._table}.state
                            ELSE %s
                        END,
                        observed = TRUE,
                        reason = COALESCE({self._table}.reason, EXCLUDED.reason),
                        actor = COALESCE({self._table}.actor, EXCLUDED.actor)
                    """,
                    (
                        run_id,
                        AbortLifecycleState.OBSERVED.value,
                        reason,
                        actor,
                        AbortLifecycleState.CANCELLED.value,
                        AbortLifecycleState.COMPLETED_BEFORE_CANCEL.value,
                        AbortLifecycleState.OBSERVED.value,
                    ),
                )
        got = self.get(run_id)
        assert got is not None
        return got

    def resolve(self, run_id: str, *, cancelled: bool) -> AbortRecord | None:
        terminal = (
            AbortLifecycleState.CANCELLED
            if cancelled
            else AbortLifecycleState.COMPLETED_BEFORE_CANCEL
        )
        with self._connect(autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE {self._table}
                    SET state = %s, observed = observed OR %s
                    WHERE run_id = %s AND state NOT IN (%s, %s)
                    """,
                    (
                        terminal.value,
                        cancelled,
                        run_id,
                        AbortLifecycleState.CANCELLED.value,
                        AbortLifecycleState.COMPLETED_BEFORE_CANCEL.value,
                    ),
                )
        return self.get(run_id)


class PostgresPlanArtifactStore(_PostgresControlStoreBase):
    """Postgres plan artifact store — durable approved-plan persistence."""

    _table_name = "plan_artifacts"

    def _init_schema(self, cur: Any) -> None:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._table} (
                plan_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )

    def put(self, artifact: PlanArtifact) -> PlanArtifact:
        with self._connect(autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO {self._table} (plan_id, run_id, created_at, payload)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (plan_id) DO UPDATE SET
                        run_id = EXCLUDED.run_id,
                        created_at = EXCLUDED.created_at,
                        payload = EXCLUDED.payload
                    """,
                    (
                        artifact.plan_id,
                        artifact.run_id,
                        artifact.created_at,
                        artifact.model_dump_json(),
                    ),
                )
        return artifact

    def get(self, plan_id: str) -> PlanArtifact | None:
        with self._connect(autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT payload FROM {self._table} WHERE plan_id = %s",
                    (plan_id,),
                )
                row = cur.fetchone()
        if row is None:
            return None
        return PlanArtifact.model_validate_json(row["payload"])

    def list_for_run(self, run_id: str) -> list[PlanArtifact]:
        with self._connect(autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT payload FROM {self._table}
                    WHERE run_id = %s
                    ORDER BY created_at ASC, plan_id ASC
                    """,
                    (run_id,),
                )
                rows = cur.fetchall()
        return [PlanArtifact.model_validate_json(row["payload"]) for row in rows]


class PostgresCommandQueueStore(_PostgresControlStoreBase):
    """Postgres durable cross-process steering command queue."""

    _table_name = "command_queue"

    @property
    def _runs_table(self) -> str:
        return f"{self._config.schema}.live_message_runs"

    @property
    def _meta_table(self) -> str:
        return f"{self._config.schema}.control_schema_meta"

    def _init_schema(self, cur: Any) -> None:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._table} (
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
            """
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._runs_table} (
                run_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            )
            """
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._meta_table} (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        cur.execute(
            f"""
            INSERT INTO {self._meta_table} (key, value)
            VALUES ('live_message_contract_version', '1')
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """
        )

    def enqueue(self, request: ControlRequest) -> CommandQueueItem:
        return self._mutate("enqueue", request)

    def admit(
        self,
        request: ControlRequest,
        *,
        accepted_phase: LiveMessagePhase | None = None,
    ) -> CommandQueueItem:
        return self._mutate(
            "admit", request, accepted_phase=accepted_phase
        )

    def get(self, queue_id: str) -> CommandQueueItem | None:
        with self._connect(autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT payload FROM {self._table} WHERE queue_id = %s",
                    (queue_id,),
                )
                row = cur.fetchone()
        if row is None:
            return None
        return CommandQueueItem.model_validate_json(row["payload"])

    def list_pending(
        self,
        *,
        run_id: str | None = None,
        thread_id: str | None = None,
        agent_id: str | None = None,
    ) -> list[CommandQueueItem]:
        with self._connect(autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT payload FROM {self._table}
                    WHERE status = %s
                    ORDER BY created_at ASC, queue_id ASC
                    """,
                    (CommandQueueStatus.QUEUED.value,),
                )
                rows = cur.fetchall()
        items = [
            item
            for row in rows
            if _matches_route(
                item := CommandQueueItem.model_validate_json(row["payload"]),
                run_id=run_id,
                thread_id=thread_id,
                agent_id=agent_id,
            )
        ]
        items.sort(key=lambda item: (_dispatch_order(item), item.sequence, item.created_at))
        return items

    def list_for_run(self, run_id: str) -> list[CommandQueueItem]:
        with self._connect(autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT payload FROM {self._table} WHERE run_id = %s "
                    "ORDER BY created_at ASC, queue_id ASC",
                    (run_id,),
                )
                rows = cur.fetchall()
        items = [
            CommandQueueItem.model_validate_json(row["payload"]) for row in rows
        ]
        items.sort(key=lambda item: (item.sequence or 2**63, item.created_at))
        return items

    def dequeue_next(
        self,
        *,
        run_id: str | None = None,
        thread_id: str | None = None,
        agent_id: str | None = None,
    ) -> CommandQueueItem | None:
        pending = self.list_pending(
            run_id=run_id, thread_id=thread_id, agent_id=agent_id
        )
        return pending[0] if pending else None

    def cancel(self, queue_id: str) -> CommandQueueItem | None:
        return self._mutate("cancel", queue_id)

    def mark_applied(
        self,
        queue_id: str,
        *,
        claimant_id: str | None = None,
        applied_phase: LiveMessagePhase | None = None,
    ) -> CommandQueueItem | None:
        return self._mutate(
            "mark_applied",
            queue_id,
            claimant_id=claimant_id,
            applied_phase=applied_phase,
        )

    def mark_failed(self, queue_id: str, *, error: str) -> CommandQueueItem | None:
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
        with self._connect(autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT payload FROM {self._runs_table} WHERE run_id = %s",
                    (run_id,),
                )
                row = cur.fetchone()
        return LiveRunState.model_validate_json(row["payload"]) if row else None

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
        return self._mutate(
            "release_claim", queue_id, claimant_id=claimant_id
        )

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
        with self._connect(autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT value FROM {self._meta_table} WHERE key = %s",
                    ("live_message_contract_version",),
                )
                row = cur.fetchone()
        return int(row["value"]) if row else 0

    def quarantine_legacy_rows(self) -> list[CommandQueueItem]:
        """Fail ambiguous pre-v1 NEXT rows while preserving their payload."""
        return self._mutate("_quarantine_legacy")

    def _mutate(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """Serialize the complete state transition in one Postgres transaction.

        A schema-scoped advisory transaction lock provides a simple, explicit
        cross-process CAS boundary. Live-message volume is tiny; correctness and
        crash recovery are more important than parallelizing mutations inside
        one run/control schema.
        """
        with self._connect(autocommit=False) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"{self._config.schema}:live-message-v1",),
                )
                memory = self._snapshot(cur)
                quarantined = _quarantine_legacy(memory)
                result = (
                    quarantined
                    if method == "_quarantine_legacy"
                    else getattr(memory, method)(*args, **kwargs)
                )
                self._persist_snapshot(cur, memory)
                return result

    def _snapshot(self, cur: Any) -> InMemoryCommandQueueStore:
        memory = InMemoryCommandQueueStore()
        cur.execute(
            f"SELECT payload FROM {self._table} ORDER BY created_at ASC, queue_id ASC"
        )
        items = [
            CommandQueueItem.model_validate_json(row["payload"])
            for row in cur.fetchall()
        ]
        items.sort(key=lambda item: (item.sequence or 2**63, item.created_at))
        memory._items = {item.queue_id: item for item in items}  # noqa: SLF001
        memory._order = [item.queue_id for item in items]  # noqa: SLF001
        cur.execute(f"SELECT payload FROM {self._runs_table}")
        states = [
            LiveRunState.model_validate_json(row["payload"])
            for row in cur.fetchall()
        ]
        memory._run_states = {state.run_id: state for state in states}  # noqa: SLF001
        return memory

    def _persist_snapshot(
        self, cur: Any, memory: InMemoryCommandQueueStore
    ) -> None:
        for item in memory._items.values():  # noqa: SLF001
            cur.execute(
                f"""
                INSERT INTO {self._table} (
                    queue_id, control_id, run_id, thread_id, agent_id, priority,
                    kind, status, source, dedupe_key, created_at, payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (queue_id) DO UPDATE SET
                    control_id = EXCLUDED.control_id,
                    run_id = EXCLUDED.run_id,
                    thread_id = EXCLUDED.thread_id,
                    agent_id = EXCLUDED.agent_id,
                    priority = EXCLUDED.priority,
                    kind = EXCLUDED.kind,
                    status = EXCLUDED.status,
                    source = EXCLUDED.source,
                    dedupe_key = EXCLUDED.dedupe_key,
                    created_at = EXCLUDED.created_at,
                    payload = EXCLUDED.payload
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
            cur.execute(
                f"""
                INSERT INTO {self._runs_table} (run_id, payload)
                VALUES (%s, %s)
                ON CONFLICT (run_id) DO UPDATE SET payload = EXCLUDED.payload
                """,
                (state.run_id, state.model_dump_json()),
            )

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


def _dispatch_order(item: CommandQueueItem) -> int:
    if item.kind.value == "interrupt":
        return 0
    if item.requested_semantic is LiveMessageSemantic.REDIRECT_CURRENT:
        return 1
    if item.requested_semantic is LiveMessageSemantic.STEER_CURRENT:
        return 2
    return 10 + _PRIORITY_ORDER[item.priority]


def _quarantine_legacy(
    memory: InMemoryCommandQueueStore,
) -> list[CommandQueueItem]:
    changed: list[CommandQueueItem] = []
    for queue_id in list(memory._order):  # noqa: SLF001
        item = memory._items[queue_id]  # noqa: SLF001
        if (
            item.schema_version == 0
            and item.status is CommandQueueStatus.QUEUED
            and item.kind.value in {"enqueue_user_message", "redirect_user_message"}
            and item.priority is ControlPriority.NEXT
        ):
            failed = memory.mark_failed(queue_id, error="legacy_unresolved")
            if failed is not None:
                changed.append(failed)
    return changed


__all__ = [
    "PostgresControlStoreConfig",
    "PostgresApprovalConsumptionStore",
    "PostgresAbortLifecycleStore",
    "PostgresPlanArtifactStore",
    "PostgresCommandQueueStore",
]
