# Memory plane: кросс-сессионная память как первоклассная подсистема

Дата создания: 2026-07-19 (из horizon-scan 020, кандидат №1). Статус: **active** —
вертикальный слайс на MeetScript реализуется вместе с этим эпиком.

Продуктовая примерка (MeetScript «Ask Meetings»): персона «забывчивый руководитель» из
Аргус-бенча — ассистент должен помнить МЕЖДУ сессиями чата, кто пользователь, какие проекты
его интересуют, какие предпочтения формата он высказывал («отвечай тезисами»), о чём уже
спрашивал. Сейчас каждая сессия — с чистого листа.

## Что в движке уже есть (ревизия 2026-07-19)

`agent_driver/memory/`: контракты MemoryProvider (prefetch/sync_turn), MemoryStore
(append/list_for_session), StoreBackedMemoryProvider (recency+keyword), Sqlite/InMemory-сторы;
`MemoryLifecycleHook` (сессия = `run_input.thread_id`, recall инъецируется с untrusted-скан
дисциплиной E3 — recalled-текст помечен как «background, не инструкции»). RunnerConfig принимает
`memory_provider`. Шов ГОТОВ — не хватает хостовых сторов, extraction-качества и семантики.

## Reference-first

- **hermes** `agent/memory_manager.py` + `plugins/memory/` (8 провайдеров: mem0, honcho,
  supermemory, …): query_rewrite перед recall, prefetch latency/budget-контракты, fail-open
  recall; honcho — кросс-сессионное моделирование ПОЛЬЗОВАТЕЛЯ (dialectic reasoning, persistent
  conclusions).
- **openclaude** `src/memdir/`: авто-память + team-memory sync (debounce anti-loop #1726),
  relevance-поиск (`findRelevantMemories`), governance записи (#1806), recovery interaction с
  autocompact (#1858).

## Фазы

A. **Host-store слайс (MeetScript)**: Mongo-стор + StoreBackedMemoryProvider, user-scoped
   `thread_id`; env-гейт; PII-дисциплина (recall уходит наружу через тот же барьер, что и весь
   prompt); live-smoke кросс-сессионного recall. ← выполняется сейчас.
B. **Extraction-качество**: не сырые ходы, а ФАКТЫ — auxiliary-model выжимка «что стоит помнить»
   (durable prefs/facts vs episodic turns), kind=FACT/PREFERENCE; дедуп и supersede (новый факт
   вытесняет устаревший — как эволюция фактов в Аргус-бенче).
C. **Семантический recall**: embedding-store (у MeetScript — Milvus рядом), query_rewrite
   (hermes), бюджет recall в токенах, fail-open при недоступности стора.
D. **Governance/UX**: команда «что ты обо мне помнишь» + забывание; лимиты на запись;
   наблюдаемость (events recall/remember, raw-free).

## Критерий ценности

Бенч-кейс: сессия 1 фиксирует факт/предпочтение → НОВАЯ сессия (без истории) отвечает с учётом.
Дальше — многосессионный сценарий персоны (интересующие проекты, форматные предпочтения).
