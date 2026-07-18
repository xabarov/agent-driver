# Provider empty/degraded response: recovery ladder v2

Дата создания: 2026-07-18. Статус: **proposed**.

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
