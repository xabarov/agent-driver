# Экономика промпт-кэша: включить, стабилизировать префикс, мерить

Дата создания: 2026-07-23 (исследование референсов, раунд 2). Статус: **proposed**.

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
