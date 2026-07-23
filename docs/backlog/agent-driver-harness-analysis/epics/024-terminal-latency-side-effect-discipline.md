# Дисциплина терминальной фазы: что имеет право блокировать завершение рана

Дата создания: 2026-07-23 (из живого инцидента MeetScript «Сохраняю прогресс»). Статус: **DONE 2026-07-23 (A-E)**.

Мотивация из живых данных: 2026-07-22 замерены хвосты **21.6 / 33.7 / 138.8s** между готовым
финальным текстом и `run_completed` — синхронная LLM-экстракция памяти + невидимый goal-gate
грейдер + немая ретрай-лестница. Точечный фикс 9d46af7 закрыл симптом (память в фон через
`defer_sync` + `on_run_completed`, событийная обвязка finalize-хуков, transient-ретрай), но
пласт остался без владельца: **нигде не сформулировано правило, что́ вообще может стоять между
финальным токеном и терминальным событием.** У обоих референсов это правило есть и оно
архитектурное, а не комментарий в коде.

## Что осталось открытым после 9d46af7 (проверено gap-картой 2026-07-23)

1. **Goal-gate грейдер по-прежнему блокирует завершение** — `dispatch_finalize` awaited до
   `dispatch_run_completed` (`steps.py:445-471`). Он и должен блокировать (может запросить
   ревизию), но у него нет ни бюджета латентности, ни таймаута, ни рекомендаций по модели.
2. **Нет таймаута finalize-хуков** — `dispatch_finalize` без `asyncio.wait_for`; медленный
   хост-грейдер может держать финализацию десятки секунд. (Audit-middleware эпика 010 имеет
   таймауты — но это другой, не-runtime путь.)
3. **Событийная обвязка только у finalize** — `dispatch_run_start` / `dispatch_tool_evidence` /
   `dispatch_run_completed` не получили `emit`; их длительности невидимы.
4. Правило «результат сначала, side effects потом» живёт только в комментариях
   (`memory_hook.py:4-12`, `steps.py:468-470`) — ни контракта, ни теста, ни доки.

## Reference-first

- **hermes `agent/turn_finalizer.py`** — единый пост-петлевой шов с явным порядком: собрать
  `result` → слить steer → и только ПОСЛЕ этого side effects; комментарий у
  `_spawn_background_review`: «runs AFTER the response is delivered so it never competes with
  the user's task». `memory_manager.py::sync_all()` документирует наш класс бага дословно
  (провайдер блокировал ~298s inline → «agent marked running for minutes»); фикс — single-worker
  `DaemonThreadPoolExecutor` (`_submit_background`, сериализация turn N перед N+1), bounded-drain
  ТОЛЬКО на shutdown с отчётом `abandoned_writes` — не per-turn.
- **hermes `agent/verify_hooks.py`** — анти-паттерн «грейдер держит завершение» снят структурно:
  verify-гейт по умолчанию НЕ добавляет второй модельный ход («does not create its own extra
  model turn»), bounded `DEFAULT_MAX_VERIFY_NUDGES=3`.
- **openclaude `src/query/stopHooks.ts`** — экстракция памяти / prompt-suggestion / auto-dream
  запускаются `void`-ом (fire-and-forget, `:178-194`); коалесинг при быстрых ходах (стэш
  `pendingContext` + abort superseded run); дрейн только на выходе процесса
  (`print.ts:1066` `drainPendingExtraction`, soft-timeout 60s через `Promise.race`).
- **openclaude prefetch-план** — `startRelevantMemoryPrefetch` (`attachments.ts:2660`):
  вспомогательные LLM-вызовы стартуют ПАРАЛЛЕЛЬНО основному запросу как disposable,
  потребляются позже только если уже готовы — ноль добавленной латентности.
- Контраст: openclaude goal-controller (`services/goal/controller.ts`) — та же наша дыра
  (awaited LLM-грейдер без живого статуса); копировать не надо, надо бюджетировать.

## Эскиз фаз

A. **Контракт терминальной фазы** (док + тест): классификация post-final работы на
   (1) блокирующую по семантике (ревизионные гейты), (2) фоновую (персистенция, экстракция,
   аудит), (3) prefetch-совмещаемую. Правило: класс (2) НИКОГДА не awaited до терминального
   события; новый хук обязан объявить класс. Тест-замок в духе `test_runtime_metadata_inventory`.
B. **Бюджет finalize**: `asyncio.wait_for` вокруг каждого finalize-хука
   (`RunnerConfig.finalize_hook_timeout`, дефолт ~15s, fail-open = принять ответ), таймаут
   эмитит `LIFECYCLE_HOOK_TIMED_OUT` (enum уже есть, не эмитится нигде).
C. **Симметрия обвязки**: `emit` для `dispatch_run_start` / `dispatch_tool_evidence` /
   `dispatch_run_completed`; порог — эмитить completed-событие только при duration > N ms,
   чтобы не зашумлять журнал.
D. **Грейдерный бюджет** (продолжение 022): рекомендации хостам — дешёвая/быстрая модель для
   грейдера, `max_tokens` малый, streaming off; коалесинг повторных грейдов; опционально
   hermes-стойка «нет второго модельного хода без явной директивы».
E. Приёмка: замер хвоста на MeetScript-классе прогонов (цель: p95 хвоста < 5s без ревизии),
   отчёт `abandoned`-синков на shutdown.

## Не в скоупе

Сама ревизионная петля (022) и лестница пустых финалов (016) — здесь только их место
на критическом пути и бюджеты.

## Реализация 2026-07-23 (фазы A-D)

- **A** Контракт: `docs/terminal-phase-contract.md` (3 класса пост-финальной работы,
  правила для авторов хуков, инвентарь) + ast-тест-замок
  `tests/runtime/test_terminal_phase_contract.py` (новый on_finalize/on_run_completed
  без записи в контракт = красный тест; обратный тест ловит «призраков» в инвентаре).
- **B** Бюджет: `RunnerConfig.finalize_hook_timeout` (дефолт 15s, None = off),
  `dispatch_finalize(..., timeout=)` — per-hook `asyncio.wait_for`, fail-open (ответ
  принят, ревизия таймаутнувшего хука отброшена), эмитится `lifecycle_hook_timed_out`
  {hook, phase, timeout_seconds}; completed-скобка при таймауте не эмитится.
- **C** Симметрия: `dispatch_run_start`/`dispatch_tool_evidence`/`dispatch_run_completed`
  получили emit; порог `_SLOW_HOOK_EMIT_MS=250` — одиночное completed-событие только
  для реально медленных хуков (finalize сохраняет полную скобку started/completed).
  Общий эмиттер `_hook_event_emitter` в steps.py + врезка в tool_stage.
- **D** Бюджет грейдера: § в terminal-phase-contract.md + аддендум в 022 (модель
  дешёвая, ≤300 токенов, temp 0, streaming off; движковый таймаут покрывает runaway;
  коалесинг = max_iterations 1; hermes-стойка «без второго хода» — как эскалация).
- Бонус hermes-паттерн: bounded drain шатдауна памяти (30s, конфигурируемо) с честным
  отчётом `abandoned` вместо вечного ожидания клина провайдера.
- Тесты: timeout-fail-open + slow-run-start-emit (test_rubric_goal_gate), bounded-drain
  (test_memory_lifecycle_wiring), ast-инвентарь ×2.

## Фаза E — живая приёмка 2026-07-23 (MeetScript dev)

Первый замер (грейдер на deepseek-v4-flash): хвосты **6.0/8.4/12.0/12.7s** — бюджет и
видимость работают, но p95<5s FAIL: грейдер сам медленный. Применена рекомендация фазы D:
замер кандидатов на промпте реального размера (overview 4k + ответ 8k) —
deepseek-v4-flash 8-19s, haiku-4.5 ~3.3s, **gemini-2.5-flash-lite 0.8-1.4s**, qwen3-32b
20s+мусор. Хост: `CHAT_V2_GOAL_GATE_MODEL` (дефолт gemini-2.5-flash-lite, пусто = модель
чата) → `LlmRequest(model=...)`.

Повторный замер (5 прогонов): хвосты **0.1/0.8/0.8/0.8/1.2s, max 1.2s — PASS p95<5s**
(исходно 21.6-138.8s, после 9d46af7 6-12.7s). Грейдер жив: в одном прогоне запросил
bounded-ревизию (две скобки rubric); fail-open в логах не срабатывал. Качество:
goal-gate-чувствительный сабсет бенча (decisions/coverage/multi_meeting, 9 кейсов) —
**9/9 PASS** на новой модели грейдера. Отчёт abandoned-синков — покрыт bounded-drain
тестом (жив в shutdown-логе).
