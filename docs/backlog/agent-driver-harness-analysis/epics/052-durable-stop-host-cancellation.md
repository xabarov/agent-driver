# U4 — Durable Stop + host cancellation

Дата создания: 2026-08-02. Статус: **IN PROGRESS — C(hook) + A/D + B(границы) + `CANCELLATION_FAILED`
DONE 2026-08-02; fencing + mid-LLM-await открыты**. Родитель:
[[048-pentestlens-embedding-readiness-goal]]. Происхождение: upstream Goal (host-adoption).

> **`CANCELLATION_FAILED` DONE** (свип 2948): timeout-под-abort (uncooperative handler, застрявший шаг)
> → `TerminalReason.CANCELLATION_FAILED` (enforced-stop) вместо `DEADLINE_EXCEEDED`; abort-ledger
> пишет observed→cancelled. Plain timeout без abort остаётся `DEADLINE_EXCEEDED`. Тест
> `tests/runtime/test_cancellation_failed.py`. **Осталось:** fencing/epoch-token + `LATE_RESULT_IGNORED`
> (новая инфра); mid-in-flight-LLM-await abort (риск CancelledError→RUNTIME_ERROR мис-маппинга).

> **Реализация A/D-ядра** (свип 2933, +14): `agent_driver.runtime.control.AbortLifecycleStore`
> (in-memory + SQLite, ре-экспорт из facade) — реальный durable lifecycle
> `requested→observed→cancelled|completed_before_cancel`, restart-queryable. `request_abort`
> (actor/reason, cross-process), `mark_observed` **выставляет `observed=True`** (transition, которого
> старая запись никогда не делала), `resolve` — правдивый терминал. Проводка: опц.
> `RunnerConfig(abort_store=...)`, `runner._finalize_abort_lifecycle` после каждого терминального
> рана (cancelled-by-user → observed+cancelled; завершился при pending-stop → completed_before_cancel;
> чистый ран → нет записи). Тесты: `test_abort_lifecycle_store.py` (state-machine+durability),
> `test_runner_abort_lifecycle.py` (проводка+restart). **B**: step-boundary-чек уже перед каждым
> plan/LLM/tool-шагом + tool-stage скипает не-начатый вызов при наблюдённом abort (`run_aborted`-блок
> из hook-среза C) = «no new work once observed» на границах.
>
> **Осталось:** наблюдение abort *в mid-in-flight-LLM-await* (responsiveness; нужен аккуратный
> terminal-mapping, чтобы cancel не стал RUNTIME_ERROR); **fencing/epoch-token** против поздних
> handler-результатов; отдельные terminal-reason'ы cancellation-failed / late-result-ignored;
> deadline на cancellation-токене (сейчас `deadline_seconds=None`).

> **Реализация hook-среза** (свип 2897, +4): governed-executor опционально принимает
> `abort_handle` (протянут из step-loop как `tool_gate`, только когда задан → старые сигнатуры
> executor'ов не тронуты) и на каждый вызов выставляет handler'у `ToolCancellation` (run/call/
> attempt-identity + `is_cancelled` + `await wait_cancelled()`) через тот же contextvar-идиом, что и
> `report_tool_progress` — `current_tool_cancellation()`. Handler'ы без обращения к токену сохраняют
> `Callable[[dict], Awaitable[dict]]` и накладных расходов не несут. Плюс: при уже-наблюдённом abort
> не-начатый вызов скипается с `run_aborted`-блоком (no new work once observed). Новый модуль
> `agent_driver.tools.cancellation`; scope/accessor в `agent_driver.tools.context`. Adapter
> `RunAbortHandle→предикат` держит `tools` развязанным с runtime-abort. Тесты:
> `tests/tools/test_tool_cancellation.py`.
>
> **Осталось A/B/D:** durable abort-lifecycle (`requested→observed→cancelled|completed_before_cancel`),
> который реально выставляет `observed` и переживает restart (сейчас `DurableAbortRequestRecord.observed`
> не выставляется, репо in-memory); наблюдение abort в mid-LLM-await (сейчас там поллится только
> redirect-probe); bounded cancellation-deadline на токене (сейчас `deadline_seconds=None`);
> terminal-честность (cancelled vs completed-before vs cancellation-failed vs late-result-ignored);
> fencing-token против поздних handler-результатов; uncooperative-handler/restart-adversarial-матрица.

Определить durable abort-lifecycle со стабильными состояниями/событиями (`abort_requested →
abort_observed → cancelled | completed_before_cancel`), queryable после restart. Abort-request
идемпотентен, durable, actor/reason/time-correlated, издаваем из другого процесса. Runner проверяет
его **перед каждым** последующим plan/LLM/tool-переходом и не даёт начать новую работу после
observed. Активная host-работа получает документированный cancellation-token/hook (run/call/attempt-
identity + bounded deadline). Terminal-исход правдиво различает cancelled / already-completed /
cancellation-failed-timed-out / late-result-ignored. Поздний handler не воскрешает ран и не
перезаписывает terminal-cancellation-запись.

## Что уже есть (не переделываем)

- **Abort-primitive** — `runtime/abort.py:66 RunAbortHandle`: `is_aborted`, `reason`,
  `abort(reason)`, `child()`, `async wait_aborted()`; thread-safe (Lock + WeakSet children),
  идемпотентный reason. Намеренно вне `AgentRunInput` (держит live-lock, не сериализуем).
- **Второй seam** — `RunnerConfig.cancellation_probe` (`single_agent/types.py:89`); оба чекаются,
  first-to-fire wins.
- **Step-boundary-чек** — `runner.py:232 _drive_steps` → `_terminal_from_limits`
  (`lifecycle/journal.py:299`, чекает probe затем `abort_handle.is_aborted` → `CANCELLED_BY_USER`).
- **Control-plane-мост (durable, cross-process)**: `drain_step_boundary_controls`
  (`control/dispatcher.py:37`) транслирует durable `ControlKind.INTERRUPT` → `abort_handle.abort()`
  (`dispatcher.py:161`); `SqliteCommandQueueStore` персистит команды (`control/sqlite.py:21`,
  `CommandQueueStatus` QUEUED/APPLIED/CANCELLED/FAILED).
- **Durable-record-тип** — `DurableAbortRequestRecord` (`contracts/durable_lifecycle.py:353`:
  `abort_request_id`, `run_id`, `reason`, `requested_at`, `requested_by`, `observed:bool`); пишется
  `server/runs.py:536 _durable_stop`.
- **Terminal-vocabulary** — `TerminalReason` (`contracts/enums/runtime.py:20`): различает
  `CANCELLED_BY_USER` vs `DEADLINE_EXCEEDED` vs `FINAL_ANSWER` и др.
- **Revival-guard'ы (process-local)**: `_terminal_from_limits` fence; `server/runs.py.stop`
  terminal-gated + force-CANCEL parked approval; resume-loop `and not record.abort.is_aborted`
  (`runs.py:304`); resume-actions → `CANCELLED_BY_USER`/`APPROVAL_REJECTED`.
- Тесты: `tests/runtime/test_abort_handle.py` (17), `test_runner_abort.py`,
  `test_abort_resume_interaction.py`, `test_control_queue.py` (SQLite-persist).

## Незакрытые gaps (этот эпик)

1. **Чек только на границах шагов, не перед каждым переходом**: `_terminal_from_limits` — раз за
   итерацию цикла, до `_execute_step`; целый шаг (LLM-call или вся tool-stage) идёт непрерывно между
   чеками. Единственное mid-step-прерывание — `_await_with_redirect_probe`
   (`llm_step/completion.py:68`), но оно поллит `redirect_probe`, **НЕ `abort_handle`** (это
   REDIRECT-путь). Голый `abort()` не наблюдается mid-LLM-await.
2. **Durable abort-lifecycle отсутствует по факту**: состояния только `requested`+`observed:bool`;
   нет `observed → cancelled | completed_before_cancel`; **`observed` нигде не выставляется в `True`**
   (переход определён, но не подключён); `DurableLifecycleRepository.aborts` — in-memory dict
   («Durable» лишь в имени); live-флаг `RunAbortHandle` исчезает при restart. Durable cross-process
   есть только через steering-очередь, и та по умолчанию `InMemoryCommandQueueStore` (opt-in SQLite).
3. **В running-handler НЕ передаётся cancellation-token**: `ToolHandler = Callable[[dict],
   Awaitable[dict]]` (`tools/registry/types.py:8`) — args in, dict out, **без context/token/
   deadline/identity**; вызов `await spec.registered.handler(spec.call.args)` (`allowed.py:231`)
   передаёт только args. In-flight cooperative host-job/socket/browser **не получает сигнала
   отмениться** — рантайм ждёт его до конца, затем видит abort на след. границе. Единственное
   форс-прерывание — wall-clock `asyncio.wait_for` (`runner.py:277`, `DEADLINE_EXCEEDED`), не
   driven by abort и без cooperative-hook. (`ToolManifest.interrupt_behavior` — про диспозицию уже
   готового результата, не abort-token.)
4. **Terminal не различает**: cancelled vs already-completed-before-cancel (поздний `.abort()` после
   `FINAL_ANSWER` даёт обычный FINAL_ANSWER); cancellation-failed/timed-out (uncooperative-handler →
   `DEADLINE_EXCEEDED`, неотличимо от любого timeout; нет «abort requested but host refused»);
   late-result-ignored (нет маркера). `server/runs.py:170 abort_requested` — server-memory-флаг, не
   terminal-reason.
5. **Нет fencing/epoch-token на tool-результатах**: поздний handler не отклоняется по identity — он
   просто не наблюдается после границы; handler, вернувшийся ДО границы после запрошенного abort,
   может сложить результат в тот шаг. Идемпотентность держится на `RunStatus`/`step_name=="done"`,
   не на durable cross-process-guard.
6. **Тест-дыры**: нет abort uncooperative/still-running-handler'а (`_SlowProvider` тормозит LLM, не
   тул); нет abort across process-restart (durability-тесты — только queue-строки, не reload
   `RunAbortHandle`/`DurableAbortRequestRecord`); `observed`-переход не тестится; cooperative-token-
   delivery не существует.

## Фазы

A. **Durable abort-lifecycle**: реальные состояния `abort_requested → abort_observed → cancelled |
   completed_before_cancel`, персистятся (durable-impl вместо in-memory `aborts`-dict; общий
   durable-store с U3), queryable после restart; выставлять `observed=True` в runner при наблюдении;
   idempotent, actor/reason/time-correlated, издаваем cross-process (через durable command-queue,
   поднять до supported; рассмотреть Postgres command-queue-impl, которого сейчас нет).
B. **Чек перед каждым переходом**: расширить abort-observation с границ шагов на pre-plan/pre-LLM/
   pre-tool; довести abort-наблюдение в mid-LLM-await (сейчас только redirect) — гарантировать «no
   new work once observed».
C. **Host cancellation-hook**: документированный token/hook, передаваемый в активный handler, с
   run/call/attempt-identity (из U2) и bounded deadline, чтобы хост отменил свой job/socket/browser.
   Опциональный расширенный `ToolHandler`-контракт (context-aware), совместимый со старым
   args-in/dict-out.
D. **Terminal-правдивость + fencing**: различать cancelled / already-completed / cancellation-failed-
   timed-out / late-result-ignored (новые terminal-reason'ы); epoch/fence-token, чтобы поздний
   handler-результат не воскрешал ран и не перезаписывал terminal-cancellation-запись; approval/resume
   после abort не воскрешает.
E. **Adversarial-тесты** (§acceptance-6): abort во время planning / approval-wait / cooperative-handler
   / uncooperative-handler / completion-race / process-restart. Assert: нет поздних tool-calls,
   стабильный terminal-readback, честный late/ignored-исход. Приёмка: свип, CHANGELOG, ledger;
   abort-lifecycle + cancellation-hook/token + terminal/late-result-контракт в handoff.

## Не в скоупе

- Реальная отмена host job/socket/browser — **хост** (движок даёт identity+hook+observe, но НЕ
  заявляет, что внешний I/O остановлен, если local runner лишь перестал ждать).
- Карантин продуктового late-evidence — хост.
