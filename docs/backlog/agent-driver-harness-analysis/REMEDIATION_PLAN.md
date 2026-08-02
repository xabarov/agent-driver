# Remediation plan — ответ на UPSTREAM_REMEDIATION_REQUEST (R0–R6)

Статус: **PLAN — принят к исполнению** · Дата: 2026-08-02
Отвечает на: `UPSTREAM_REMEDIATION_REQUEST.md` (blocking remediation, downstream gate `PentestLens / EPIC-03A`)
Аудит подтверждён фактами (см. вердикт-верификацию ниже) — план не оспаривает требования, а закрывает их.

## Верификация аудита (почему план, а не спор)

| Треб. | Проверенный факт | Источник |
|---|---|---|
| R5/R4 | `embedding.py` **отсутствует** в релизном `7ff876a` (появился в `7bf1c6d`); U4 deadline-populate введён в `d43720d` — оба **после** cut → wheel не содержит весь код Goal | `git cat-file`, `git log 7ff876a..HEAD` |
| R2 | В коде только `ApprovalConsumptionStore` Protocol + `InMemory` + `Sqlite`; Postgres-impl нет. Исходный контракт (стр.160-168) требует Postgres/production-store + тесты против реального бэкенда; non-goal (стр.257) запрещает in-memory-only как доказательство | `grep`, `git show 62c6ba8~1:…upstream-requirements.md` |
| R0 | `62c6ba8` правил входной контракт in-place (+61/−20), вписал `✅ DONE`/«IMPLEMENTED» прямо в заголовки требований | `git show --stat 62c6ba8` |
| R1/R3 | terminal/trace-проекции + adversarial-матрица — TODO по нашему же handoff §7 | `handoff-0.2.0` §7 |
| R6 | Эпики **050/051/053 = `IN PROGRESS`**, при `DONE` в требованиях; 048 = `PROPOSED` | `grep` статусов эпиков |

Вывод: аудитор прав по существу; фактических ошибок в аудите не найдено.

## Принцип ремедиации

1. **Входной контракт неизменяем.** `upstream-requirements.md` возвращается к утверждённому тексту
   (SHA `d4ed6c…`) и далее трактуется как read-only input. Весь статус/evidence — только в эпиках,
   capability-ledger и новом handoff.
2. **Никакого самозакрытия.** Пункт закрывается только после исполнения исходного текста + acceptance.
   Ослабления (напр. SQLite вместо Postgres) — не применяются без согласования с владельцем PentestLens.
3. **Один release SHA.** Весь required-код (включая уже готовые post-cut U1/U4) + новый код R1–R4
   собираются в один чистый source-commit; wheel воспроизводим и соответствует ему.
4. **402-патч — вне этого Goal.** Отдельный логический коммит, не в release случайно.

## Карта R → эпики

| R | Эпик | Название | Тяжесть | Закрывает/чинит | Зависимости |
|---|---|---|---|---|---|
| R0+R6 | **056** | Contract integrity & status reconciliation | S (docs) | in-place правки `62c6ba8`; статусы 048–055 | 056a — сразу; 056b — последним |
| R1 | **057** | ToolGate provenance — full lifecycle (terminal+trace+matrix) | M–L | доводит 050 (U2 `IN PROGRESS`) | — |
| R2 | **058** | Postgres durable atomic approval (+ abort/plan PG-сторы) | **L** (наибольший) | доводит 051 (U3), расширяет 052/053 durability | — |
| R3 | **059** | Plan policy binding через checkpoint/resume/trace | M | доводит 053 (U5 `IN PROGRESS`) | — |
| R4 | **060** | U4 Stop-контракт: полная матрица + весь код в артефакте | S–M | верифицирует 052 (U4), закрывает trace/terminal-щели | 057 (trace-хелперы) |
| R5 | **061** | Единый согласованный release + handoff (repro wheel) | M | пересборка из полного SHA | **057–060 + 056a done** |
| — | (не-Goal) | 402 credit-retry — отдельный коммит | XS | вне контракта | — |

Зонтичный **048** остаётся open: переводится `PROPOSED → IN PROGRESS (remediation phase)`; `complete`
только после acceptance R0–R5 (закрывает 056b/R6).

## Порядок исполнения

```
Фаза 0 (сразу, параллельно): 056a (R0)  +  402-коммит (не-Goal)
Фаза 1 (параллельно, независимые подсистемы): 057 (R1) ‖ 058 (R2) ‖ 059 (R3)
Фаза 2 (после 057): 060 (R4)  — использует trace-проекции из 057
Фаза 3 (после 057–060): 061 (R5) — release из одного SHA + repro wheel + handoff
Фаза 4 (последним): 056b (R6) — сверка статусов эпиков/ledger/handoff с фактом → 048 complete
```

Крит. путь: **058 (Postgres)** и **057 (provenance-matrix)** — самые долгие; 061 их ждёт.

---

## Эпик 056 — R0+R6: contract integrity & status reconciliation

**Файлы:** `upstream-requirements.md`, `epics/048–055`, `capability-ledger.md`, новый handoff.

**056a (R0 — сразу):**
- `git revert`/ручной откат правок `62c6ba8` в `upstream-requirements.md` → вернуть текст с SHA `d4ed6c…`;
  зафиксировать его как read-only input (баннер «approved input, do not edit; status → epics/handoff»).
- Проверка: `sha256sum upstream-requirements.md == d4ed6c371eda50e6c0b7fa07df55974cfac7411e32a95708a3f203cbcd526316`.
- Статусы/evidence перенести в отдельный `status-ledger.md` (или в сами эпики).

**056b (R6 — последним, после R1–R5):**
- Привести 048–055 к фактическому terminal-статусу; ни один обязательный пункт не помечен `optional/non-blocking`.
- `capability-ledger.md` и handoff называют одинаковые остаточные риски; убрать ложные заявления о Postgres/trace/release.
- 048 aggregate-DoD закрыть ссылками на реальные тесты/коммиты.

**DoD R0:** утверждённый SHA снова однозначен; нет self-cert `DONE`; отдельный handoff ссылается, не меняя смысл.
**DoD R6:** статусы согласованы; obligatory ≠ optional; нет ложных заявлений.

---

## Эпик 057 — R1: ToolGate provenance, полная lifecycle-проводка

**Что уже есть:** `tool_call_id`/`attempt_id` в `ToolGateContext`; `GateProvenance(decision_id,
policy_snapshot_id, metadata)` → `_ad_gate_provenance` (reserved `_ad_`); `ensure_bounded_json_metadata`
fail-closed; проводка в approval-interrupt + envelope. **Щель:** terminal projection + trace/support
projection + полная adversarial-матрица (handoff §7).

**Фазы:**
- A. **Terminal projection.** Провести provenance в terminal-outcome/`RuntimeDecision`-проекцию;
  gate-DENY отличается от static-DENY. Evidence-receipt несёт `tool_call_id` + `decision_id`.
- B. **Trace/support projection (redaction-safe).** Provenance в trace без утечки host-секретов;
  тест на redaction.
- C. **Identity-инварианты.** Один `tool_call_id` стабилен gate→terminal; `attempt_id` меняется
  только на новой исполнительной попытке (не на retry-той-же-операции).
- D. **Adversarial-матрица** (Acceptance R1): allow / deny / ask+resume / retry / failure / timeout /
  abort; malformed/oversized/non-JSON/reserved-key metadata → детерминированный fail-closed;
  model/tool-metadata не может создать/перезаписать host-provenance; нет contradictory identity и
  required skip/xfail.

**Acceptance (1:1 с R1):** стабильный `tool_call_id` gate→terminal; `attempt_id` только на новой попытке;
provenance в checkpoint+events+envelopes+traces+terminal; host-provenance неперезаписываема из model/tool;
fail-closed матрица; полное покрытие allow/deny/ask/retry/failure/timeout/abort + redaction-safe trace.

---

## Эпик 058 — R2: полный Postgres-backed control plane  ⟵ наибольший

**Scope (подтверждён):** PG-impl для **всех четырёх** durable control-сторов —
`Approval`, `Abort`, `PlanArtifact`, `CommandQueue`. Один PG cluster, **отдельная generic schema**
`agent_driver_control` (own DDL/migration, изолирована от продуктовых таблиц), **без PentestLens-семантики**,
**без общей транзакции с продуктовыми таблицами** (каждый стор атомарен сам по себе).

**Что уже есть:** `ApprovalConsumptionStore` Protocol + `InMemory` + `Sqlite` (CAS `INSERT OR IGNORE`);
`AbortLifecycleStore`, `PlanArtifactStore`, `SqliteCommandQueueStore` — Protocol/SQLite/in-mem;
`ResumeCommand.{idempotency_key, expected_checkpoint_id, expected_revision}`; prior-result replay;
`CheckpointRef.revision`. **Паттерн PG:** `agent_driver/runtime/postgres_store.py` (`PostgresRuntimeStore`
для checkpoint) — по нему делаем 4 control-стора (async pg, connection-pool, DDL bootstrap).

**Фазы:**
- A. **Generic schema + bootstrap.** `agent_driver_control` schema + идемпотентный DDL (4 таблицы:
  `approval_consumption`, `abort_lifecycle`, `plan_artifact`, `command_queue`); версия схемы; общий
  PG-connection/pool-хелпер (переиспользуем из `postgres_store.py`).
- B. **`PostgresApprovalConsumptionStore`**: txn + unique-constraint/CAS (`INSERT … ON CONFLICT DO NOTHING`
  `RETURNING`), exactly-once consume; строка пишется **до** исполнения тула (crash-safe). Атомарно связывает:
  session/run + interrupt + logical tool-call id + expected checkpoint id + monotonic revision + host
  idempotency key + decision kind + gate provenance + terminal consumption/replayable result.
- C. **`PostgresAbortLifecycleStore`**: `requested→observed→cancelled|completed_before_cancel`,
  restart-queryable, idempotent, actor/reason/time-correlated; monotonic transitions через CAS.
- D. **`PostgresPlanArtifactStore`**: persist approved-plan `plan_id`/`content_hash`/`revision`/policy-binding
  на approve/reject; читается после restart (feeds R3).
- E. **`PostgresCommandQueueStore`**: durable cross-process command-queue (enqueue/claim/ack) с
  at-least-once claim + idempotent ack; поднимает U4 cross-process abort-издание до durable PG.
- F. **Экспорт** всех четырёх через supported facade `agent_driver.runtime` + export-snapshot update.
- G. **Two-client / real-Postgres acceptance** (marker `postgres`, БЕЗ секретов и цели): два независимых
  процесса/клиента approve один interrupt → ровно один side-effect; duplicate idempotency key → прежний
  результат verbatim, тул не повторяется; conflicting decision/key и stale checkpoint/revision → стабильный
  явный conflict/stale; approval после reject/abort/timeout/новее-revision/terminal → не оживляет;
  crash после consume до HTTP-ответа → безопасный retry без второго side-effect; аналог. гонки для
  abort-CAS и command-queue-claim.
- H. **Backend-параметризация**: одна тестовая матрица гоняется и на in-memory, и на реальном Postgres.

**Acceptance (1:1 с R2):** PG-impl всех 4 сторов с transaction/unique/CAS через supported facade;
two-client race → один winner; duplicate → replay verbatim; conflict/stale → стабильно; no-revival;
crash-safe retry; зелено на in-memory И на Postgres.

---

## Эпик 059 — R3: plan policy binding в checkpoint/resume/trace

**Что уже есть:** harness-authored `content_hash` (+ EDIT re-hash); `detect_plan_revision`;
`PlanningPolicyInput.required_plan_hash` (gate DENY на ревизии); opaque host policy-binding;
durable `PlanArtifactStore` на approve/reject. **Щель:** binding+identity через **trace projection**
(handoff §7).

**Фазы:**
- A. **Persist через checkpoint/resume**: `plan_id` + `content_hash` + `revision` + host policy-binding
  переживают checkpoint persistence и resume (не только helper/in-mem dict).
- B. **Trace projection (redaction-safe)**: binding присутствует в runtime/trace-проекции execution-journal.
- C. **Overwrite-guard**: попытки перезаписи binding/hash из model/tool payload отклоняются/игнорируются (тест).
- D. **Re-approval on EDIT**: материальная ревизия меняет authoritative hash и до tool-execution требует
  нового approval по host policy (тест через реальный checkpoint/resume/trace путь).

**Acceptance (1:1 с R3):** binding+identity переживают checkpoint+resume; в redaction-safe trace;
overwrite отклоняется; EDIT→новый hash→re-approval до execution; тест через реальный путь, не helper.

---

## Эпик 060 — R4: U4 Stop-контракт целиком в релизном артефакте

**Что уже есть (052, `DONE`):** durable abort lifecycle, result fencing, `CANCELLATION_FAILED`,
mid-LLM abort, bounded cancellation deadline (`d43720d`). **Щель:** (1) весь U4-код должен попасть в
release-SHA (сейчас deadline — post-cut); (2) полнота матрицы + terminal/late-result-различение;
(3) при необходимости — trace-проекция U4-исходов (переиспользует 057).

**Фазы:**
- A. **Полнота матрицы** (Acceptance R4): abort во время planning / approval-wait / LLM-await /
  cooperative-handler / uncooperative-handler / completion-race / process-restart; проверить, что все
  ветки реально покрыты (не xfail).
- B. **Terminal-различение**: cancelled / completed-before-cancel / cancellation-failed / late-result-ignored —
  различимы в terminal-outcome; после observed-abort **не** начинается ни один network/tool action.
- C. **Identity+deadline в токене**: host cancellation-token несёт run/call/attempt-identity + конечный deadline (из budget).
- D. **В артефакт**: гарантировать, что весь U4-набор (incl. deadline wiring) в release-source-commit (координируется с 061).

**Acceptance (1:1 с R4):** wheel из commit со всем U4; матрица покрыта; terminal различает 4 исхода;
no-new-action после abort; token с identity+deadline.

---

## Эпик 061 — R5: единый согласованный release + handoff

**Зависит от:** 057–060 + 056a закрыты, весь required-код в дереве, worktree чист (402-патч уже отдельным коммитом).

**Фазы:**
- A. **Версия**: выбрать следующую корректную pre-1.0 по release-policy. Рекоменд.: **`0.3.0`** — после
  `0.2.0` добавлены новые публичные символы (`agent_driver.embedding`) ⇒ minor-bump, не patch; `0.2.0`
  identity/wheel/hash **не переиспользуются**. (Решение по версии — за владельцем.)
- B. **Синхронизация identity**: package version == `agent_driver.__version__` == wheel METADATA ==
  changelog == docs (guard-тест `test_version.py` + export-snapshot).
- C. **Post-cut namespace**: включить `agent_driver.embedding` + соответствующий exact export-snapshot.
- D. **Reproducible wheel**: две изолированные сборки с фикс. `SOURCE_DATE_EPOCH` → идентичный SHA-256.
- E. **Handoff-receipts**: exact filename/size/SHA-256/`SOURCE_DATE_EPOCH`/Python/builder + команды
  проверки imports/METADATA; различить release-source-SHA и (если есть) более поздний doc-commit;
  привести результаты всех проверок из «Обязательной итоговой проверки» (10 пунктов запроса).

**Acceptance (1:1 с R5):** идентичности совпадают; exact release-commit содержит весь код+тесты;
repro-wheel идентичен; handoff с полными receipts; полный unit-suite + adversarial + lint/type/docs +
Python-matrix зелёные; `git status --porcelain` пуст; public GitHub commit доступен; нет required-кода
только в notes/patch/следующем unreleased-коммите.

---

## CI: обязательный Postgres job (подтверждено)

- Отдельный **обязательный** job поднимает pinned **`postgres:15-alpine`**, экспортит DSN, гоняет
  `pytest -m postgres`. Default-suite (`pytest` без `-m postgres`) остаётся быстрым — PG-тесты исключены
  дефолтно через `addopts`/marker-конфиг.
- **Fail-not-skip guard:** если DSN не задан / драйвер не установлен / ни один `postgres`-тест не собран —
  job **падает**, а не проходит зелёным. Реализация: conftest в PG-режиме (напр. env `AD_REQUIRE_POSTGRES=1`)
  превращает отсутствие DSN/драйвера в `error`, а не `skip`; отдельный assert «собрано ≥ N postgres-тестов».
- `postgres` регистрируется в `pyproject [tool.pytest.ini_options].markers` (секция уже есть).
- **Handoff (R5):** приводит фактически выполненную команду job'а + результат real-Postgres matrix
  (кол-во тестов, pass, версия PG), не «должно работать».

## Не-Goal: 402 credit-retry — отдельный коммит

Уже реализовано в этой сессии (уточнённая `reduced_after_provider_402` до affordable-N; CHANGELOG
`[Unreleased]`; тест `test_credit_error_clamps_below_floor_to_stated_affordable_budget`). Закоммитить
**отдельным логическим изменением** с собственным changelog/test evidence; **не** включать в
remediation-release для «чистого worktree».

## Терминальное условие Goal (из запроса)

Закрыть только когда одновременно: R0–R6 без ослаблений; U2/U3/U4/U5 доказаны сквозными durable-тестами;
весь required-код в одном release-SHA; wheel воспроизводим и соответствует SHA; handoff с проверяемыми
identity + полными receipts; GitHub-branch и локальный checkout чисты; нет required-TODO в
handoff/эпиках/notes/skipped-tests/unreleased-коммитах. Затем PentestLens независимо проверяет по
неизменённой копии контракта и переводит EPIC-03A в `in_progress`.

## Подтверждённые решения (владелец, 2026-08-02)

1. **Версия релиза = `0.3.0`.** `0.2.0` identity/wheel/hash **не переиспользуются**.
2. **R2 объём = полный Postgres-backed control plane:** `Approval`, `Abort`, `PlanArtifact`,
   **`CommandQueue`** — все четыре durable-стора получают PG-impl. Один PG cluster, **отдельная
   generic schema** (напр. `agent_driver_control`), **без PentestLens-семантики** и **без требования
   общей транзакции с продуктовыми таблицами** (сторы самодостаточны). ⇒ 058-B перестаёт быть опцией,
   входит в scope.
3. **PG в CI = внешний pinned `postgres:15-alpine` в отдельном ОБЯЗАТЕЛЬНОМ job, marker `postgres`.**
   Default-suite остаётся быстрым (PG-тесты исключены дефолтно). PG-job обязан **fail, а не skip**, при
   отсутствии DSN / dependency / тестов. Handoff содержит фактически выполненную команду + результат
   real-Postgres matrix.
