# Provider empty/degraded response: recovery ladder v2

Дата создания: 2026-07-18. Статус: **done** (2026-07-18, вечер).

> Реализация: фаза A — `ProviderErrorReason.EMPTY_RESPONSE` + `_EMPTY_RESPONSE_MARKERS`,
> проверяемые РАНЬШЕ overflow во всех путях классификации (422/400/generic), retryable без
> compress (4 теста). Фаза B — prior-turn substantive fallback в force-final (floor 200 chars,
> сигнал `forced_final_recovered_prior_turn`, metadata-провенанс). Фаза C —
> `RunnerConfig.fallback_providers` → `RunnerDeps` → ступень fallback-провайдера (folded
> request, сигнал + `forced_final_fallback_provider`); метрика ступеней через
> `empty_forced_final_retry`. Фаза D — провенанс-гарантия «ретраи только на копиях»
> (тест неизменности исходной истории) + runner-уровневый e2e с эмулятором deepseek-пустот
> (`_EmptyFinalProvider`): лестница проходит, скаффолдинг не течёт в main-loop запросы.
> Итог лестницы: non-stream → no-tools → history-fold → fallback-provider → prior-turn →
> честный сигнал. Полная регрессия runtime/llm/contracts зелёная.

Источник: MeetScript «Аргус»-бенч — deepseek-v4-flash отдаёт пустой финальный completion после
tool-цикла; текущие 3 стратегии (non-stream retry → no-tools retry → history-fold, 86a4424)
иногда исчерпываются, и ран завершается пустым (хост маскирует честным notice). Замер: ~1/99
прогонов. Reference pull 2026-07-18 показал, что у обоих референсов эта плоскость глубже.

## Reference-first (свежие реализации)

- **hermes `agent/conversation_loop.py` ~5150-5383** — 6-ступенчатая лестница: (a)
  `fallback_prior_turn_content` (если предыдущий ход уже дал реальный текст рядом с
  housekeeping-тулами — взять его, не ретраить); (b) post-tool nudge (синтетический
  `assistant("(empty)")` + user «process the tool results and continue»); (c) thinking-prefill
  continuation ×2; (d) plain retry ×3; (e) **fallback-провайдер из `_fallback_chain`**; (f)
  честный sentinel. Синтетика помечена `_empty_recovery_synthetic` и вычищается перед персистом
  (`run_agent.py:_drop_trailing_empty_response_scaffolding`) — «continue» не реплеит мусор.
- **hermes `agent/error_classifier.py`** (свежие 032a424fa/862b1b37b) —
  `_EMPTY_PROVIDER_RESPONSE_PATTERNS` проверяются РАНЬШЕ overflow-паттернов: пустой ответ
  ретраится как server_error, НЕ загоняет сессию в compress-death-spiral.
- **openclaude `src/services/api/claude.ts:2578-2605`** — stopReason-aware детекция обрыва
  стрима (легитимный пустой end_turn ≠ обрыв) + non-streaming fallback + stream-watchdog.

## Что у agent-driver уже есть / чего не хватает

Есть: non-stream retry, no-tools retry, history-fold, `forced_final_empty_after_all_retries`,
LlmStreamIdleTimeout, partial-final recovery (≥200 chars). Не хватает:
1. **Prior-turn content fallback** — если в ЭТОМ ране уже есть substantive assistant-текст,
   финализировать им, а не пустотой (у MeetScript есть host-side аналог для completion-stub —
   должен жить в движке).
2. **Fallback-провайдер/модель** как последняя ступень (RunnerConfig.fallback_provider_chain).
3. **Классификатор**: наш generic «LLM completion failed» не различает empty vs overflow vs
   transport — портировать паттерн-таблицу hermes (empty раньше overflow, retryable без compress).
4. **Провенанс синтетики ретраев** + очистка перед checkpoint-персистом (resume не должен
   видеть retry-скаффолдинг).

## Фазы

A. Классификатор ошибок провайдера (empty-паттерны, приоритет над overflow) + тесты.
B. Prior-turn substantive fallback в force-final (порог/условия как у hermes).
C. Fallback-chain (config), метрики по ступеням лестницы (какая спасла).
D. Скаффолдинг-провенанс + очистка при персисте; e2e на deepseek-эмуляторе пустых ответов.

## Живое свидетельство 2026-07-22 (MeetScript dev, вес для приоритета)

Диагностика «ассистент висит на "Сохраняю прогресс"»: у прогона со списком инструментов
хвост после готового текста составил **139 секунд**; в контрольном прогоне лестница видна
целиком в журнале событий: пустой forced-final стрим (20:48:50) → non-stream retry (7s,
вернул tool-call-shaped финал) → no-tools retry (16s) → `assistant_message_replaced`
(20:49:13). Т.е. лестница РАБОТАЕТ, но каждая ступень — полный повторный вызов deepseek,
и до 2026-07-22 ступени были невидимы пользователю (warning-события не показывались в UI
MeetScript; поправлено на стороне хоста). Приоритет фаз A/B подтверждён живым продом:
классификатор + prior-turn fallback срезали бы две ступени из трёх.
