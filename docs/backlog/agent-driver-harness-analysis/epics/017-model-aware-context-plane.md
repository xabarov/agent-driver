# Model-aware context plane: окно из каталога, буферные пороги, компакт-предохранители

Дата создания: 2026-07-18. Статус: **done, фазы A-C** (2026-07-18, поздний вечер); фаза D
(models.dev snapshot) — опциональная, не реализована.

> Реализация: фаза A — `agent_driver/llm/context_windows.py` (resolve: точный каталог с
> алиасами/вендор-префиксами → семейная таблица → None; floor 16k) +
> `TrimmingSettings.resolved_for_model` (провенанс default/explicit/model_catalog; явный
> host-тюнинг всегда побеждает) + автоприменение в request-build с observability-штампом
> `context_window_resolved`. Фаза B — guard пустого результата session-memory компактизации
> (`_has_sendable_content`: system-only/пустой набор НЕ заменяет промпт, счётчик брейкера,
> сигнал `compaction_empty_result_skipped`) + cooldown/half-open у circuit-breaker'а
> (`cooldown_attempts`, полуоткрытая проба, re-open с новым cooldown). Фаза C —
> тест-инварианты порогов на окнах 12k..1M (порядок, headroom ≥ reserve). Регрессия
> runtime/context/llm/contracts зелёная (кроме 2 предсуществующих phase6-фейлов).

Источник: MeetScript-инцидент «12k-окно на 128k-модели» (пороги token-pressure наследовали
дефолт → compact/blocking на ~10k → run_failed). Починено точечно
(`TrimmingSettings.for_context_window`, 3c8a8d4) — но хост ОБЯЗАН вручную прокидывать размер
окна (env CHAT_V2_CONTEXT_WINDOW_TOKENS), хотя **provider_catalog.py уже содержит per-model
context_window** (128k/200k/262k/400k) и просто не консультируется рантаймом.

## Reference-first

- **hermes `agent/models_dev.py`** — реестр models.dev (4000+ моделей): bundled snapshot →
  disk cache → network → фоновый refresh 60 мин; `agent/model_metadata.py` — floor
  MINIMUM_CONTEXT_LENGTH=64k, семейные fallback-словари, live-probe локальных vLLM/LM-Studio,
  endpoint-scoped резолвинг.
- **openclaude `src/utils/context.ts`** — приоритет: env-cap → session-override
  (`/set-context-window`) → per-model runtime limits → fallback; `autoCompact.ts` — порог =
  window − buffer (buffer 13k→30k, поднят чтобы компакт срабатывал РАНЬШЕ и не рос latency);
  **circuit-breaker компакта**: 3 подряд фейла → cooldown 5 мин → half-open.
- **hermes `agent/context_engine.py`** — плагинный движок компактизации (config-selectable).

## Чего не хватает agent-driver

1. **Авторезолв окна**: runtime при построении TrimmingSettings должен брать окно из
   provider_catalog по (provider, model), с приоритетом: явный host-override → каталог →
   консервативный дефолт; floor как у hermes.
2. **Буферная модель порогов** вместо чистых процентов: reserve растёт с окном (у нас уже
   window//32 — сверить с buffer-подходом openclaude, где буфер абсолютный и поднят ради latency).
3. **Circuit-breaker компактизации** (3 фейла → cooldown → half-open) — прямой ответ на наш
   хостовый workaround «compaction выключен из-за empty message set»; цель — вернуть хостам
   компактизацию безопасно.
4. Каталог: дозаполнить окна моделей, используемых хостами (deepseek-v4-flash и т.п.), и
   рассмотреть лёгкую интеграцию с models.dev snapshot (без сети в рантайме по умолчанию).

## Фазы

A. Резолвер окна из каталога + приоритеты + тесты (проверка: MeetScript может удалить env).
B. Компакт-circuit-breaker + фикс «empty message set» корня (см. host-комментарий в chat.py).
C. Буферная калибровка порогов, метрика «сколько latency съедает поздний компакт».
D. (Опционально) models.dev snapshot как источник каталога.
