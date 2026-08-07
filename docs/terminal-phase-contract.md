# Контракт терминальной фазы (эпик 024)

Что имеет право стоять между финальным токеном ответа и терминальным событием
(`RUN_COMPLETED`). Мотивация: живой инцидент 2026-07-22 — хвосты 22–139s после уже
готового ответа из-за небюджетированных awaited side effects (LLM-экстракция памяти,
LLM-грейдер goal-gate, немые ретраи). Референсы: hermes `turn_finalizer.py` («result
сначала, side effects после, на background-executor»), openclaude `stopHooks.ts`
(fire-and-forget + drain-at-exit).

## Классы пост-финальной работы

| Класс | Что это | Правило | Механика |
|---|---|---|---|
| **1. Блокирующая по семантике** | Ревизионные гейты: результат хука может ИЗМЕНИТЬ ответ (goal-gate/rubric запрашивает ревизию) | Может блокировать, но ТОЛЬКО под бюджетом: `RunnerConfig.finalize_hook_timeout` (дефолт 15s, per-hook, fail-open = ответ принят) + обязательная событийная обвязка `lifecycle_hook_started/completed/timed_out` | `dispatch_finalize(..., timeout=...)` |
| **2. Фоновая** | Персистенция/экстракция/аудит: результат хука НЕ меняет ответ | НИКОГДА не awaited до терминального события. Планируется задачей; flush — в `shutdown()` (bounded drain + отчёт abandoned) и перед recall следующего рана (read-your-writes) | `on_run_completed` + `defer_sync=True` у провайдера; `MemoryLifecycleHook._pending_syncs` |
| **3. Prefetch-совмещаемая** | Вспомогательные LLM/IO-вызовы, чей результат нужен ПОЗЖЕ (recall следующего хода, подсказки) | Стартует параллельно основной работе, потребляется только если уже готова; отменяется с раном | образец: openclaude `startRelevantMemoryPrefetch` (disposable). У нас пока нет общего примитива — кандидат следующей фазы |

## Правила для авторов хуков

1. Новый lifecycle-хук с пост-финальной работой обязан объявить свой класс в
   **инвентаре ниже** — тест `tests/runtime/test_terminal_phase_contract.py` не даст
   добавить `on_finalize`/`on_run_completed` молча.
2. `on_run_completed` — только класс 2: внутри допускаются лишь дешёвые синхронные
   операции и `asyncio.create_task`; медленный хук виден по
   `lifecycle_hook_completed{phase=run_completed, duration_ms}` (эмитится при
   duration ≥ 250ms) — это сигнал нарушения, а не норма.
3. `on_finalize` — только класс 1, и только если результат реально может изменить
   ответ. Наблюдателям (аудит/метрики) в finalize делать нечего — им место в классе 2.
4. Провайдер памяти с LLM/сетевыми вызовами в `sync_turn` обязан ставить
   `defer_sync = True` (см. `FactExtractingMemoryProvider`).

## Бюджет грейдера (фаза D, дополнение к эпику 022)

Goal-gate грейдер — единственный легитимный блокирующий LLM-вызов терминальной фазы.
Рекомендации хосту:

- **Модель**: дешёвая/быстрая (grader-вызов — классификация, не генерация); не
  reasoning-модель. `max_tokens` малый (≤300), `temperature=0`, streaming off.
- **Бюджет**: движковый `finalize_hook_timeout` покрывает runaway (fail-open); хосту
  не нужен собственный таймаут, но нужен fail-open в самом грейдере (ошибка = satisfied).
- **Коалесинг**: `max_iterations=1` по умолчанию (одна bounded-ревизия); не грейдить
  повторно неизменившийся ответ.
- **Safety/quality gate**: для правила, которое запрещено нарушать в успешном
  ответе, хост возвращает `RevisionRequest(disable_tools=True,
  max_revisions=1, fail_closed=True, gate_id="...")`. Ревизия получает прежний
  ответ и feedback, но не видит tool schemas и не может выполнить новый tool
  call. Возвращённый synthesis-only ответ передаётся непосредственно тому же
  finalize-гейту: generic continuation detector и node-contract reprompt не
  вправе вставить между ними ещё одну, не проверенную гейтом генерацию.
  Повторное нарушение завершается `guardrail_blocked`, а не молчаливым
  принятием невалидного ответа.
- Альтернативная стойка (hermes `verify_hooks`): гейт вообще не добавляет второй
  модельный ход без явной директивы — рассматривать, если латентность грейдера станет
  доминировать даже под бюджетом.

## Инвентарь пост-финальных хуков

Каждый класс движка, переопределяющий `on_finalize` или `on_run_completed`
(тест-замок сверяет этот список с ast-сканом кода):

- `MemoryLifecycleHook` — класс 2 (`on_run_completed`, defer_sync-провайдеры в фон;
  bounded drain 30s в shutdown с отчётом abandoned).
- `RubricLifecycleHook` — класс 1 (`on_finalize`, goal-gate; бюджетируется
  `finalize_hook_timeout`).
