# Model-aware context plane: окно из каталога, буферные пороги, компакт-предохранители

Дата создания: 2026-07-18. Статус: **proposed**.

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
