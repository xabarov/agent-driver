# R2 — Full Postgres-backed control plane (Approval / Abort / PlanArtifact / CommandQueue)

Статус: **pending (критический путь)**. Доводит эпик 051 (U3), расширяет durability 052/053.
Контекст: `../REMEDIATION_PLAN.md` §058.

## Scope (подтверждён владельцем)

PG-impl **всех четырёх** durable control-сторов. Один PG cluster, **отдельная generic schema**
`agent_driver_control` (own DDL, изолирована от продуктовых таблиц), **без PentestLens-семантики**,
**без общей транзакции с продуктовыми таблицами** (каждый стор атомарен сам по себе).

## Что уже есть (не переделываем)

- `agent_driver/runtime/control/`: `protocols.py`, `in_memory.py`, `sqlite.py`, `approval_store.py`,
  `abort_store.py`, `dispatcher.py`. ApprovalConsumption/Abort/Plan/CommandQueue — Protocol + SQLite + in-mem.
- **PG-паттерн:** `agent_driver/runtime/postgres_store.py` (psycopg v3, lazy `_pg_dependencies()`,
  `schema`-config + `auto_create_schema`, SQL в `runtime/storage/postgres_sql.py`).
- `ResumeCommand.{idempotency_key, expected_checkpoint_id, expected_revision}`; prior-result replay;
  `CheckpointRef.revision`.

## Фазы

- A. **Generic schema + bootstrap** — schema `agent_driver_control` + идемпотентный DDL (4 таблицы);
  версия схемы; общий PG-pool/connection-хелпер (переиспользуем из `postgres_store.py`).
- B. **`PostgresApprovalConsumptionStore`** — txn + unique/CAS (`INSERT … ON CONFLICT DO NOTHING RETURNING`),
  exactly-once; строка пишется **до** исполнения тула (crash-safe). Связывает: session/run + interrupt +
  logical tool-call id + expected checkpoint id + monotonic revision + host idempotency key + decision kind +
  gate provenance + terminal consumption/replayable result.
- C. **`PostgresAbortLifecycleStore`** — `requested→observed→cancelled|completed_before_cancel`,
  restart-queryable, idempotent, actor/reason/time-correlated; monotonic transitions через CAS.
- D. **`PostgresPlanArtifactStore`** — persist approved-plan `plan_id`/`content_hash`/`revision`/policy-binding
  на approve/reject; читается после restart (feeds R3).
- E. **`PostgresCommandQueueStore`** — durable cross-process queue (enqueue/claim/ack), at-least-once claim +
  idempotent ack; поднимает U4 cross-process abort-издание до durable PG.
- F. **Экспорт** всех 4 через supported facade `agent_driver.runtime` + export-snapshot update.
- G. **Two-client / real-Postgres acceptance** (marker `postgres`, без секретов/цели): two-client approve
  → один side-effect; duplicate idempotency key → replay verbatim, тул не повторяется; conflicting
  decision/key + stale checkpoint/revision → стабильный явный conflict/stale; approval после
  reject/abort/timeout/новее-revision/terminal → не оживляет; crash после consume до HTTP-ответа →
  безопасный retry без второго side-effect; аналог. гонки для abort-CAS и command-queue-claim.
- H. **Backend-параметризация** — одна тестовая матрица гоняется и на in-memory, и на реальном Postgres.

## CI (подтверждён)

Отдельный **обязательный** job: pinned `postgres:15-alpine`, экспорт DSN, `pytest -m postgres`.
Default-suite быстрый (PG исключён дефолтно). **Fail-not-skip:** нет DSN/драйвера/собранных тестов → job
падает (env `AD_REQUIRE_POSTGRES=1` → отсутствие = error, + assert «≥N postgres-тестов собрано»).
`postgres`-marker в `pyproject`. Handoff приводит фактическую команду + результат matrix.

## Acceptance (1:1 с R2)

PG-impl всех 4 сторов с transaction/unique/CAS через supported facade; two-client race → один winner;
duplicate → replay verbatim; conflict/stale → стабильно; no-revival; crash-safe retry; зелено на
in-memory И на реальном Postgres.

## Не в скоупе

Общая транзакция с продуктовыми таблицами; PentestLens-семантика; durable Gateway (Option 2 остаётся).
