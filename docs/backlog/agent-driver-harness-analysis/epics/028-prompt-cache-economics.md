# Экономика промпт-кэша: включить, стабилизировать префикс, мерить

Дата создания: 2026-07-23 (исследование референсов, раунд 2). Статус: **DONE 2026-07-23 (A-F)**.

Мотивация: инвентаризация показала парадокс — **движок уже умеет почти всё, хост не включает
ничего**. В движке: 3-tier Anthropic `cache_control` (tools→system→transcript,
`providers_impl/anthropic.py`), `cache_read/creation_tokens` в UsageSummary, `CostLedger`
с `cache_hit_rate()`, cache-safe params в subagents-пути, чтение `cached_tokens` у
OpenAI-compat. В MeetScript `enable_prompt_cache` — **0 вхождений**; каждый ход шлёт
system + обзор корпуса + retrieval-контекст заново за полную цену. Эпик 026 сделал
префикс стабилизируемым (история = messages, конверт = request_only_context) — теперь
кэш можно реально включить. Остаток 026: usage-события хоста не несут cached-поля.

## Reference-first

- **hermes `agent/prompt_caching.py`** — стратегия `system_and_3` (4 breakpoint'а: system +
  последние 3 содержательные message), заявленный эффект ~75% экономии инпута на мультиходе;
  `_can_carry_marker()` не тратит breakpoint'ы на пустые tool-call ходы; **OpenRouter-квирк:
  top-level cache_control на role:tool молча вешает запрос** — маркер внутрь content-part.
  `usage_pricing.normalize_usage()` — унификация трёх провайдерных диалектов cached-полей;
  прайс-таблица с cache_read_cost per-model.
- **openclaude `services/api/promptCacheBreakDetection.ts`** — форензика поломок кэша:
  pre-call снапшот хэшей (system/tools/per-tool schema/cache_control-with-TTL/betas) +
  post-call сравнение cacheReadTokens → классификация причины поломки (schema-change /
  ttl-expiry / provider-instability / unknown-local-mutation) + unified diff в файл.
  «77% поломок — изменение описания одного тула».
- **openclaude размещение**: ровно ОДИН message-маркер на запрос (последнее сообщение);
  eligibility 1h-TTL **латчится на сессию** (mid-session переключение = -20K токенов);
  tool-schema база кэшируется и не мутируется (`utils/api.ts:261`); `cacheSafeParams` —
  бандл параметров для форков, чтобы побочные вызовы не трогали префикс основного треда.
- **openclaude `commands/cache-probe`** — эмпирическая проба «провайдер реально кэширует?»
  (два одинаковых вызова через 3s, сравнение cached_tokens) + `cacheMetrics.ts` пер-провайдерные
  ярусы надёжности (unsupported/advisory/reliable) — ровно наш OpenRouter-вопрос.

## Эскиз фаз

A. **Телеметрия сквозная** (остаток 026): cache_read/creation из провайдерных ответов →
   UsageSummary → host usage-события → «Сведения о запуске» (cached N% / cache-write N).
   Без замера всё остальное — вслепую.
B. **OpenRouter-проба**: cache-probe команда (двойной вызов, сравнение cached) по нашим
   моделям (deepseek-v4-flash, gemini-flash-lite, sonnet) — выяснить, кто реально кэширует
   через OpenRouter; результат — в provider_route_profiles (новое поле cache_reliability).
C. **Размещение маркеров для OpenAI-compat/OpenRouter пути**: перенести hermes
   `system_and_3` (+ анти-пустышечный отбор носителей, + tool-role квирк) в наш
   openai_compatible адаптер за `enable_prompt_cache`; Anthropic-адаптер уже готов.
D. **Хост-адопция**: `enable_prompt_cache=True` в chat_v2 RunnerConfig + дисциплина
   префикса: стабильный system (conversation_mode), стабильные tool-схемы, конверт
   request_only_context ПОСЛЕ стабильной истории (уже так по построению 026). Гейт
   CHAT_V2_PROMPT_CACHE (dev on / prod off), приёмка по фазе A метрике.
E. **Форензика поломок** (openclaude-класс, урезанная): pre-call хэш (system/tools/model) в
   metadata + WARNING signal `prompt_cache_broken` c классификацией при падении cached_tokens
   >5% и >2000 токенов — в реестр status-protocol.
F. Приёмка: замер cache-hit-rate на 10-ходовой живой сессии Ask Meetings до/после
   (цель: cached ≥50% инпута со 2-го хода) + бенч-свип без регрессий.

## Не в скоупе

Cost-роллапы и insights — эпик 032; paging больших тул-результатов — 029 (но его
frozen/mustReapply-дисциплина обязана уважать кэш из этого эпика).

## Дополнение 2026-07-23 (раунд 2b: hermes 8fc278207 + openclaude 01a01fb)

- **Провайдеро-осознанная расстановка брейкпоинтов** — hermes `agent/prompt_caching.py`
  `_can_carry_marker`/`_apply_cache_marker`: на envelope-раскладке (OpenRouter!) top-level
  `cache_control` на сообщении с пустым контентом (assistant pure-tool_calls, пустой
  `role:tool`) молча ТЕРЯЕТ один из 4 брейкпоинтов, а на `role:tool` OpenRouter
  **зависает**. Предикат пропускает такие носители и метит последний content-part
  list-контента. Прямо наш стек — портировать при включении кэша (~120 LOC чистых функций).
- **Классификатор слома кэша** — openclaude `promptCacheBreakDetection.ts`: пре-колл
  хэши фрагментов префикса (system/tools/betas, отдельно cacheControlHash), пост-колл
  триггер только при падении cache_read >5% И >2000 токенов; вердикты
  expected_local_change / ttl_expiry / provider_instability / unknown_mutation;
  per-tool хэши называют, ЧЬЯ схема поехала. Минимальная версия для нас: хэш префикса
  за ход + warning при неожиданном дрейфе.
- **Дисциплина side-вызовов**: openclaude promptSuggestion.ts:308 — «не переопределяй НИ
  ОДИН параметр» (замерен 45x спайк cache-write, hit-rate 92.7%→61% от одного различия);
  тулы запрещать callback'ом, не обрезкой массива; `skipCacheWrite` для fire-and-forget.
  Субстрат — эпик 034.
- **Честность метрик**: openclaude `CacheMetricsReliability` supported/advisory/unsupported —
  UI показывает «N/A», а не лживые «0%», когда провайдер не отдаёт cache-поля (наш
  OpenRouter-путь местами именно такой).

## Реализация и приёмка 2026-07-23 (agent-driver 16f2dd4+266401f, хост, пин 0.1.0+g266401f3)

- **A** Телеметрия сквозная: `extract_cache_token_fields` — 3 диалекта
  (prompt_tokens_details.cached_tokens / cache_read+creation_input_tokens /
  prompt_cache_hit_tokens) → UsageSummary.cache_read/creation_tokens → хостовый
  usage-payload (cache_read_tokens, cached_percent) → «Сведения о запуске»
  («кэш N%» / честное «кэш N/A», когда провайдер молчит). Live: cached-поля
  видны в run_completed каждого хода.
- **B** Проба (docs/reliability/scripts/openrouter_cache_probe.py, живой прогон):
  **sonnet-4.6 — explicit, 18047/18065 (99.9%) прочитано 2-м вызовом с нашей
  маркер-схемой; gemini-2.5-flash-lite — implicit, 94% cached без маркеров;
  deepseek-v4-flash — none-observed (поля отдаёт, cached=0 на идентичных вызовах;
  латентности 122-138s в тот же прогон)**. Поле `cache_reliability` добавлено в
  ProviderRouteProfile (+to_metadata).
- **C** system_and_3 в openai-compat payload за request.enable_prompt_cache:
  носители — не role:tool (hang-квирк), не пустые pure-tool_calls (потеря
  брейкпоинта), маркер на последнем text-парте. 7 тестов.
- **D** Хост: CHAT_V2_PROMPT_CACHE (dev on / prod off) → RunnerConfig.enable_prompt_cache;
  префикс стабилен (026 conversation-first).
- **E** Форензика: prompt_cache_state fingerprint (model+system+tools) + WARNING
  `prompt_cache_broken` при >5% И >2000-токенном падении на неизменном префиксе;
  в реестре status-protocol.
- **F** Приёмка: 3-ходовая живая сессия — раны с маркерами на deepseek живы,
  телеметрия честная (cached 0.0%). Регрессионный сабсет 7/8; единственный минус
  (no_data_marketing) **A/B-эксонерация: падает и с ВЫКЛЮЧЕННЫМИ маркерами** —
  ортогональный дрейф (ответ grounded, чек строг), кандидат в чат-бэклог.
  **Целевая метрика «cached ≥50% со 2-го хода» на текущей главной модели
  недостижима не по вине плоскости: deepseek через OpenRouter не кэширует
  (проба B). Машинерия доказана на sonnet (99.9%) и gemini (94%); выигрыш
  включается сменой/маршрутизацией модели — smart-routing (020 horizon №3) и
  cost-план (032) получают готовое основание.**
