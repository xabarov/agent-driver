# U6 — Gateway truthfulness

Дата создания: 2026-08-02. Статус: **DONE (Option 2) 2026-08-02**. Родитель:
[[048-pentestlens-embedding-readiness-goal]]. Происхождение: upstream Goal (host-adoption).

> **Реализация Option 2** (свип 2907, +3): `AgentGateway.durable_parked_runs = False` — явная
> декларация, что parked-состояние process-local и теряется при restart;
> `require_durable_recovery()` фейлит deployment-readiness (`GatewayError`), когда нужна durable-
> recovery. Module-docstring документирует поддерживаемую альтернативу — **прямой embedding-путь**
> (durable `SqliteRuntimeStore`/`PostgresRuntimeStore` + `SqliteCommandQueueStore` + `RunAbortHandle`),
> который уже отдаёт все durable-примитивы без Gateway. Поведение `submit`/`respond`/`pending` не
> изменено. Тесты: `tests/gateway/test_gateway_durability.py` (readiness-reject + прямой путь отдаёт
> примитивы). Option 1 (durable parked-backend) сознательно НЕ делали — не раздуваем Goal в
> server-rewrite (PentestLens не берёт Gateway на MVP).

Не выдавать process-local `_parked`-состояние за restart-safe. Выбрать одно: **(1)** добавить durable
parked-run-бэкенд поверх atomic-approval/control-протокола (U3) с restart+concurrent-тестами; **или
(2)** явно задокументировать Gateway как process-local/non-durable, фейлить deployment-readiness при
требовании durable-recovery, и гарантировать, что supported **прямой** embedding-путь выставляет все
durable-примитивы, нужные PentestLens. **Option 2 достаточен и предпочтителен** (PentestLens не берёт
Gateway на MVP; не расширять Goal в server-rewrite).

## Что уже есть (не переделываем)

- **Прямой embedding-путь УЖЕ отдаёт durable-примитивы** (это ядро Option 2): durable checkpoint-stores
  (`SqliteRuntimeStore`/`JsonlCheckpointStore`/Postgres, protocol `runtime/storage/protocols.py:32`),
  durable steering (`SqliteCommandQueueStore`, `runtime/control/sqlite.py:21`), abort
  (`RunAbortHandle` + `handle.abort()`), resume/approve (`Agent.resume/approve`, `sdk/agent.py:394,427`).
  Хост с `SqliteRuntimeStore`+`SqliteCommandQueueStore` получает restart-safe checkpoint/resume/
  steering/abort **без Gateway**.
- `Agent`-facade wiring: `command_queue_store`-параметр+property+`control()`; checkpoint-store через
  `self._runner.deps.checkpoint_store`.

## Незакрытые gaps (этот эпик)

1. **`AgentGateway._parked` — process-local dict** (`gateway/gateway.py:55`, ключ `(session_id,
   run_id)`), `_Parked` держит только `run_id`+`interrupt_id`. `__init__` берёт лишь `agent`+
   `tool_gate` — **нет store-параметра**. На restart `_parked` пуст → каждый `respond`/`pending`
   для ранее-parked-рана падает `GatewayError`. Ничто не перестраивает `_parked` из checkpoint/command-
   store.
2. **Нет restart-recovery и нет тестов**: `tests/gateway/test_gateway.py` не содержит restart/recover/
   durable/reload-сценариев.
3. **Docs выдают Gateway за durable** (по факту требования) — надо явно исправить статус.

## Фазы (Option 2 — предпочтительный)

A. **Явная документация non-durability**: в `docs/embedding.md`/gateway-доках зафиксировать
   `AgentGateway` как **process-local/non-durable**; deployment-readiness-чек фейлит Gateway, когда
   требуется durable-recovery.
B. **Гарантия прямого пути**: подтвердить (тестом readiness-reject), что прямой embedding-путь
   выставляет все durable-примитивы, нужные хосту (checkpoint/command/approval/abort из U3/U4) —
   Gateway не нужен для durable-recovery.
C. **Тест** (§acceptance-8): либо Gateway-restart-readiness-тест (при Option 1), либо **explicit
   readiness-rejection**-тест (Option 2): попытка durable-recovery через Gateway → ясный отказ, а
   через прямой путь → успех. Приёмка: свип, CHANGELOG, ledger; Gateway durability-status в handoff.

> Option 1 (durable parked-backend) — только если независимо оправдан и близок; **не** раздувать
> Goal в server-rewrite ради PentestLens.

## Не в скоупе

- Durable-Gateway-переписывание при полном прямом пути (non-goal родителя).
- Server-транспорт-фичи сверх правдивого durability-статуса.
