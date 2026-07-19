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
   prompt); live-smoke кросс-сессионного recall. — **DONE 2026-07-19**: движок 0bd30ca добавил
   `app_metadata["memory"]` overrides (user_text/recall_query) — RAG-хост хранит ЧИСТЫЙ вопрос,
   не составной prompt; MeetScript 8b90bda (MongoChatMemoryStore, гейт CHAT_V2_MEMORY,
   thread_id=user_{id}); live-смоук PASS (факт из сессии A вспомнен в новой сессии B),
   в Mongo — чистые реплики.
B. **Extraction-качество**: не сырые ходы, а ФАКТЫ — auxiliary-model выжимка «что стоит помнить»
   (durable prefs/facts vs episodic turns), kind=FACT/PREFERENCE; дедуп и supersede (новый факт
   вытесняет устаревший — как эволюция фактов в Аргус-бенче). — **DONE 2026-07-19** (e9d1d04):
   `FactExtractingMemoryProvider` — LLM-выжимка (JSON facts + slot), supersede append-only на
   recall (новейший факт по slot побеждает), fail-open к raw-turn; MeetScript подключает через
   тот же privacy-aware провайдер (гейт CHAT_V2_MEMORY_EXTRACTION).
C. **Семантический recall**: embedding-store (у MeetScript — Milvus рядом), query_rewrite
   (hermes), бюджет recall в токенах, fail-open при недоступности стора. — **DONE 2026-07-19**:
   движок — `recall_max_chars` бюджет recall-блока (хук читает с провайдера); хост — embedding
   при append (EmbedRouter за PII-барьером) + cosine-rerank при prefetch, слоистый fail-open
   semantic → keyword → recency (гейт CHAT_V2_MEMORY_SEMANTIC). query_rewrite отложен
   (вопрос пользователя уже передаётся как recall_query через app_metadata).
D. **Governance/UX**: команда «что ты обо мне помнишь» + забывание; лимиты на запись;
   наблюдаемость (events recall/remember, raw-free). — **DONE 2026-07-19**: движок —
   `memory_recall_count` (raw-free) в context.metadata + inventory; хост — GET/DELETE
   /chat_v2/memory (owner-scoped) + UI «Память ассистента…» (просмотр фактов, «Забыть всё…»
   за ConfirmDialog); лимит записи — cap 200 записей/пользователя в Mongo-сторе.

## Критерий ценности

Бенч-кейс: сессия 1 фиксирует факт/предпочтение → НОВАЯ сессия (без истории) отвечает с учётом.
Дальше — многосессионный сценарий персоны (интересующие проекты, форматные предпочтения).
