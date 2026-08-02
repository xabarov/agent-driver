# U3 — Atomic approval and resume

Дата создания: 2026-08-02. Статус: **IN PROGRESS — контракт-срез (A) + B/C/D-ядро DONE 2026-08-02;
prior-result-replay + монотонный revision открыты**. Родитель:
[[048-pentestlens-embedding-readiness-goal]] (крупнейшая дыра Goal'а). Происхождение: upstream Goal.

> **Реализация B/C/D-ядра** (свип 2919, +12): `agent_driver.runtime.control.ApprovalConsumptionStore`
> (in-memory + SQLite, ре-экспорт из facade `agent_driver.runtime`) — durable **CAS-ledger**: первый
> `try_consume` interrupt'а выигрывает через атомарный `INSERT OR IGNORE` (SQLite) / lock-insert
> (in-memory); дубль/конфликт проигрывает и отклоняется `ResumeConflictError` **до** второго запуска
> тула = exactly-once, переживает crash-между-consume-и-result (строка пишется ДО исполнения).
> Встроен опц. `RunnerConfig(approval_store=...)`, консультируется в `_handle_resume_with_pending`;
> без стора (default) — прежнее TOCTOU+expected-checkpoint (обратно-совместимо). Тесты
> (`test_approval_consumption_store.py`, `test_resume_atomic_store_integration.py`) доказывают **один**
> side-effect тула под 16-поточной SQLite-гонкой, двумя concurrent async-resume, restart
> (новый инстанс видит consumption), конфликт-решениями, idempotency-key-дублями.
>
> **Осталось:** prior-result **full replay** (ledger хранит result_ref; вернуть прежний
> `AgentRunOutput` verbatim пока не проведено); монотонный checkpoint-revision вместо статичного
> `state_version="v1"`.

> **Реализация контракт-среза** (свип 2893, +4): `ResumeCommand` получил `idempotency_key` +
> `expected_checkpoint_id` (оба optional → обратно-совместимо). Новый `ResumeConflictError`
> (`<: RuntimeExecutionError`) на: **(1)** stale — `expected_checkpoint_id` ≠ checkpoint pending-
> interrupt'а (optimistic concurrency); **(2)** уже-консьюмнутый interrupt (матч по interrupt_id
> ИЛИ `idempotency_key` → HTTP-дубль опознаётся даже с другим interrupt_id). Консьюм пишется в
> durable `consumed_approvals` (checkpoint-metadata) → дубль-approval = идемпотентный no-op, тул НЕ
> переисполняется. Тесты: `tests/runtime/test_resume_atomic_approval.py` (доказывают один side-effect
> при duplicate/stale). Guard'ы обновлены (schema-snapshot, runtime-metadata inventory).
>
> **Осталось B/C/D:** истинный **concurrent CAS** и **Postgres durable approval-store** (сейчас
> дедуп — TOCTOU-после-коммита + optimistic expected-checkpoint, НЕ защищает pre-commit гонку двух
> клиентов); **привязка interrupt↔реальный checkpoint_id** (сейчас sentinel `checkpoint_pending` →
> host берёт id из стора); **prior-result replay** (сейчас конфликт явный, но прежний результат не
> возвращается); **crash-after-consume effect-ledger** (exactly-once); монотонный checkpoint-revision
> вместо статичного `state_version="v1"`; two-client/restart-матрица против реального durable-store.

Определить durable generic approval-record/command-протокол, привязанный к session/run+interrupt/
call-identity, expected-checkpoint-id/revision, host-supplied idempotency-key, decision-kind +
validated gate-provenance, и recorded terminal consumption-identity. Consume должен быть **атомарен
против durable-бэкенда**: ровно один approval переводит parked-работу; дубликат с тем же
idempotency-key возвращает прежний результат и НЕ переисполняет тул; конфликт/stale-checkpoint →
стабильный explicit-исход; approval после reject/abort/timeout/newer-checkpoint/terminal не воскрешает
ран; crash-after-consume-before-response ретраится без второго исполнения тула.

## Что уже есть (не переделываем)

- **Interrupt/resume-механика** — `contracts/interrupts.py`: `InterruptRequest` (L180:
  `interrupt_id`, `run_id`, `attempt_id`, `checkpoint_id`, `proposed_action` с `tool_call_id`);
  `ResumeCommand` (L130: `interrupt_id`, `action`, `edited_tool_args`, `approved_by`, `metadata`).
  Ask-gate строит interrupt (`policy_interrupt.py:15`), park сериализует pending
  (`lifecycle/pending.py`), run терминирует PAUSED (`finalization/output.py:533`), resume
  (`lifecycle/resume.py`, `SingleAgentResumeMixin`) + SDK `resume/approve/reject/edit/cancel`
  (`sdk/agent.py:394-471`).
- **Durable checkpoint-stores** (protocol `runtime/storage/protocols.py:32`): `SqliteRuntimeStore`
  (`sqlite_store.py:73`), `PostgresRuntimeStore` (`postgres_store.py:133`, `autocommit=False`+commit,
  `ON CONFLICT (checkpoint_id) DO UPDATE`), `JsonlCheckpointStore`. Events имеют `(run_id, seq)`
  unique-index (`postgres_sql.py:28`).
- **Durable command-queue** (protocol `runtime/control/protocols.py:10`): `SqliteCommandQueueStore`
  (`control/sqlite.py:21`, table `command_queue`, `ControlRequest.dedupe_key`).
- **Subagent-store — единственный с реальной idempotency-инфрой**: `SqliteSubagentStore`
  (`subagents/store.py:77`) с partial `UNIQUE INDEX (parent_run_id, idempotency_key)` — **паттерн
  для копирования** (но управляет child-spawn-дедупом, не approval-consume).
- **Durable-lifecycle-контракты**: `DurableInterruptRecord`/`DurableApprovalRecord` +
  PENDING→RESOLVED/APPROVED-переходы (`contracts/durable_lifecycle.py:245,290`) — типы существуют.

## Незакрытые gaps (этот эпик)

1. **Consume НЕ атомарен и НЕ one-time**:
   - Нет expected-checkpoint/revision-чека: единственный матч — `interrupt_id`-равенство
     (`resume.py:207-213`); `CheckpointRef.state_version` статичен `"v1"`
     (`checkpoint_factory.py:52`) — не монотонный revision-token.
   - Нет idempotency-key на `ResumeCommand` (`ControlRequest.dedupe_key` — для steering-очереди,
     не approvals).
   - Дубль-approval предотвращается только **TOCTOU-чтением `latest()`-checkpoint**: consume —
     in-memory `context.metadata["pending_interrupt"]=None`, персистится позже новым checkpoint'ом;
     второй resume ловится только ПОСЛЕ коммита post-consume-checkpoint. Нет CAS/unique/row-lock на
     самом consume.
   - **Два клиента, аппрувящих один interrupt конкурентно**: оба читают тот же `latest()`, оба
     матчат `interrupt_id`, оба ставят `approved_tool_call` → **тул исполняется дважды**.
2. **`save()` минтит новый `checkpoint_id=f"chk_{uuid4().hex}"` каждый раз** (`checkpoint_factory.py:39`)
   → PK всегда уникален → `ON CONFLICT/INSERT OR REPLACE` **никогда не срабатывает**, каждый save —
   append новой строки. Нет unique-constraint на run-state, нет CAS на «текущую» позицию, нет
   optimistic-lock-колонки.
3. **Durable approval-репозиторий не durable и не подключён**: `DurableLifecycleRepository`
   (`harness/durable_lifecycle.py:62`) — in-memory dicts (`self.approvals`/`self.interrupts`);
   `upsert_*` — наивная dict-запись без CAS/unique/tx; `resume_plan` — advisory-планирование,
   **не консультируется runner-consume-путём**. SQLite/Postgres-impl этого репозитория **нет**;
   `server/runs.py` объявляет `durability_level="process_local"`/`"server_memory"`.
4. **Существующие guard'ы process-local и per-transport**: `RunManager.approve`
   (`server/runs.py:231`, per-record `asyncio.Future`, один процесс); `AgentGateway.respond`
   (`gateway.py:75`, `_parked`-dict, `del` перед resume → crash mid-resume теряет parked-запись);
   прямой `agent.resume` — вообще без guard сверх TOCTOU.
5. **Crash-after-consume-before-result не обработан**: consume+исполнение тула происходят ДО записи
   post-tool-checkpoint (`steps.py:126`); crash между ними → latest-checkpoint всё ещё показывает
   pending → re-resume **переисполняет тул**. Нет exactly-once, нет dedupe-token, нет
   completed-effect-ledger. `DurableSideEffectSafety` — advisory-verdict, не enforcement.
6. **Тест-дыры**: нет two-client-конкуренции; нет approval/resume против реального durable-store
   (Postgres-live-тест — только checkpoint/event round-trip); durable approval-семантика тестится
   только против in-memory-репозитория.

## Фазы

A. **Approval-record-протокол**: durable generic record, привязанный к session/run+interrupt/call-id,
   **expected-checkpoint-id/monotonic-revision**, **host-idempotency-key**, decision-kind + validated
   gate-provenance (из U2), recorded terminal consumption-identity. Ввести монотонный revision на
   checkpoint (заменить статичный `state_version="v1"`; стабильный «current-position»-ключ вместо
   всегда-нового `chk_`-id, чтобы upsert/CAS реально контендил).
B. **Atomic consume против durable-бэкенда**: transactional CAS / unique-constraint (паттерн
   `SqliteSubagentStore` partial-unique-index). Durable-impl approval-репозитория:
   **in-memory (unit) + Postgres (canonical prod)** с транзакционным compare-and-swap. Ровно один
   approval транзитит parked-работу; дубль с тем же key → **прежний recorded-результат, без
   переисполнения**; конфликт-key/mismatch-checkpoint → стабильный explicit conflict/stale-исход;
   approval после reject/abort/timeout/newer-checkpoint/terminal — отклонён.
C. **Crash-safety / exactly-once**: completed-effect-ledger (или idempotent-consume-token), чтобы
   crash-after-consume-before-result ретраился без второго исполнения тула. Consume и запись
   consume-факта — в одной транзакции ИЛИ через idempotency-fence на исполнении тула.
D. **Adversarial-тесты** (§acceptance-4,5): два независимых клиента/процесса аппрувят один interrupt;
   duplicate HTTP-style-ретраи; конфликтующие решения; stale-revision; restart-between-consume/result;
   **счётчик побочного эффекта тула == 1**. Тесты гоняются **и против реального durable-store**
   (Postgres/SQLite), не только in-memory-модели.
E. Приёмка: свип, CHANGELOG, ledger; approval-record + CAS/idempotency/stale-контракт +
   durable-backend в handoff. НЕ заявлять durability от process-local dict/queue.

## Не в скоупе

- Смысл decision-kind/policy — хост (непрозрачно).
- Durable-Gateway (U6 решает отдельно; здесь durable-примитивы для **прямого** пути).
- HTTP-транспорт-семантика approvals — хост; движок даёт durable-контракт + idempotency-key.
