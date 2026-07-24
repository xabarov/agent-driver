# Фоновая консолидация памяти и self-improvement review

Дата создания: 2026-07-23 (исследование референсов, раунд 2). Статус: **DONE (A-E)** 2026-07-24.

## Итог (2026-07-24)

Наша память ≠ референсы (Mongo/SQLite стор фактов, не файловая память и не форк агента),
поэтому изоляционный чек-лист hermes выполняется **по построению**: консолидатор — один
`aux`-вызов (034-субстрат), а НЕ форк рана → журнал/кэш родителя не тронуты, cost мержится
тегом `memory_consolidation`. 4 фазы openclaude (Orient→Gather→Consolidate→Prune) свёрнуты в
один structured-emit над фактами сессии.

- **A. Seam (движок)**: `MemoryProvider.consolidate(session_id, *, cost_ledger)` (опц., дефолт
  `None`) + `MemoryStore.replace_session()` (opt-in, `hasattr`-gate → инертно на append-only
  сторе). Триггер из `MemoryLifecycleHook` по **host-supplied** `app_metadata["memory"]
  ["turn_ordinal"]` (движок stateless между ходами — счётчик не может жить на хуке), chained
  ПОСЛЕ deferred-sync в одной фоновой задаче (не гонит append↔replace), дренаж тем же bounded
  shutdown; per-session `asyncio.Lock`. `RunnerConfig.memory_consolidation_every_n_turns` (0=off).
- **B. Консолидатор** (`memory/consolidation.py`): детерминированный pre-pass (supersede_by_slot
  + exact-dedup) — если сократил, персистится ДЁШЕВО без LLM (`reason="deterministic"`); иначе
  при открытом гейте — aux `structured_completion` (036-канал) сливает cross-slot дубли, роняет
  опровергнутое (higher-id = newer), абсолютизирует относительные даты (RU/EN-маркеры), re-slot
  raw_fallback. Консервативно: guards never wipe / never grow, rollback на aux-сбой (с падением
  на детерминированный floor), value-aware cap. Cheap-first гейт (`_MIN_RECORDS_FOR_PASS=4` или
  relative-date маркер).
- **C. Сигнал поправок**: extraction-промпт (021) дополнен классом «пользователь поправил
  ассистента (формат/термин/факт, фрустрация)» → durable-факт высокого приоритета (hermes
  «frustration is a first-class signal»).
- **D. Хост**: гейт `CHAT_V2_MEMORY_CONSOLIDATION` + `_EVERY_N` (dev on/prod off);
  `MongoChatMemoryStore.replace_session` с **archive-снапшотом** (hermes «archive restorable»)
  + governance-meta + **cross-process Mongo-лок** (mirror `claim_upload_attempt`, claim_id+stale-TTL);
  `turn_ordinal` в app_metadata; `GET /chat_v2/memory.consolidation`; нотис «Последняя уборка
  памяти: <дата> (N→M)» в MemoryDialog (governance-UI, НЕ в чате). Плюс **placeholder-скраб**:
  консолидированный факт с остаточным PII-плейсхолдером (`@@…@@`) откатывается к чистому
  архивному оригиналу по слоту, иначе выбрасывается — память никогда не показывает оператору
  сырой плейсхолдер (аномалия на приёмке = блокер, Принцип №2). Скраб чинит и предсуществующую
  «отравленную» память.
- **E. Приёмка**: 13 движковых тестов (fixture: merge/drop-contradiction/relative-date/empty-guard/
  would-grow-guard/aux-fail/store-without-replace/cadence×5) + 3 host-теста (гейт-каданс/turn_ordinal/
  scrub-planner); **live через реальный build-путь** (реальный Mongo+LLM+privacy-барьер): 6→4,
  контрадикция убрана, cross-slot merge, archive-снапшот, governance-meta, ноль плейсхолдеров;
  **операторский GET/DELETE** на реальной сессии `user_1` вернул непустой `consolidation` нотис.

## Найдено на приёмке (follow-up)

Structured-emit через privacy-барьер иногда не восстанавливает PII-плейсхолдер в tool-call
args → он оседает в памяти (нашлись и предсуществующие «отравленные» raw_fallback-записи, не от
031). Скраб 031 закрывает это на стороне памяти; общий фикс восстановления tool-call args в
`restore_strings` — кандидат в отдельный эпик privacy-провайдера.

Мотивация: наша память (021/027) — per-turn экстракция фактов + supersede + гигиена
recall. Чего нет: **периодической уборки стора** (дубли между слотами, осиротевшие
raw-fallback записи, противоречия, рост до капа 200 записей с вытеснением по FIFO, а не
по ценности) и **обучения на поправках пользователя** (исправил формат/термин — никто
не дистиллирует это в durable-знание). Оба референса решают это одним механизмом:
фоновый ревьюер-форк после хода/каждые N ходов.

## Reference-first

- **hermes `agent/background_review.py`** (991 строк) — форк полного агента в daemon-треде
  каждые ~10 ходов (`memory.nudge_interval`), читает разговор и решает «что сохранить/
  обновить в памяти/скиллах». **Промпт-дисциплина**: поправки/фрустрация пользователя —
  первоклассный сигнал; предпочтение «обнови существующее» перед «создай новое»;
  запрет одноразовых артефактов. **Изоляционный чек-лист** (выстрадан инцидентами):
  shared session_id ради тёплого кэша, НО persist-disabled (журнал пользователя не
  загрязняется — иначе «агент становится куратором»), tool-whitelist {memory,
  skill_manage} с auto-deny остального, compression off (форк не должен выиграть гонку
  компрессии у родителя), max_iterations 16, thread-scoped silence. Замер PR #17276:
  cache-parity форка = ~26% экономии e2e. Итог-сводка действий → ненавязчивое
  уведомление «💾 Self-improvement review: …».
- **openclaude `services/autoDream/consolidationPrompt.ts`** — 4-фазный проход:
  Orient (индекс памяти) → Gather (свежие логи, дрейфующие записи, узкий grep
  транскриптов) → Consolidate (слияние в топики, относительные даты → абсолютные,
  удаление опровергнутого) → Prune & index (индекс ≤25KB, строки ≤150 симв., чистка
  битых указателей). Лок консолидации против конкурентных dream'ов; коалесинг
  (superseded-прогон абортится, один хвостовой добег).

## Эскиз фаз

A. **Движок, seam консолидации**: `MemoryProvider.consolidate(session_id)` (опциональный
   метод) + триггер из MemoryLifecycleHook по кадансу (`RunnerConfig.memory_consolidation_
   every_n_turns`, дефолт 10, 0=off) — планируется фоновой задачей по правилам
   terminal-phase контракта (класс 2), лок per-session против конкурентных прогонов.
B. **Консолидатор для FactExtracting-стека**: LLM-проход (aux-модель) над стором сессии —
   слить дубли между слотами, убить опровергнутые (нашли supersede-пару без слота),
   конвертировать относительные даты, пере-слотировать raw_fallback записи; бюджет ≤1
   вызов, ≤N записей за проход. Изоляция по hermes-чек-листу: отдельный LLM-вызов (не
   форк рана) → журнал не затрагивается по построению.
C. **Сигнал поправок**: экстракционный промпт (021) дополнить классом «пользователь
   поправил ассистента (формат/термин/факт)» с высоким приоритетом слота — дешёвый шаг
   к self-improvement без skills-подсистемы.
D. **Хост**: каданс-гейт CHAT_V2_MEMORY_CONSOLIDATION (dev on / prod off), Mongo-лок,
   уведомление в governance-UI памяти (не в чат) о выполненной уборке; кап стора при
   вытеснении предпочитает удалять raw_fallback/устаревшие, а не свежие слоты.
E. Приёмка: сид-стор с дублями/противоречиями/relative-датами → консолидация даёт
   ожидаемый результат (фикстурный тест) + живой прогон: после 10+ ходов Аргус-бенча
   стор компактнее и recall чище (clarify_when-класс остаётся PASS).

## Не в скоупе

Skills-библиотека (horizon №6), полный dream по файловой памяти (у нас стор в Mongo).

## Дополнение 2026-07-23 (раунд 2b)

- **openclaude auto-dream — готовый рецепт ночной консолидации** (`autoDream.ts` +
  `consolidationPrompt.ts`): порядок гейтов cheap-first (время ≥24ч → счётчик сессий ≥5 →
  кросс-процессный lock), read-only-принуждение инструментов через canUseTool, rollback
  при сбое; промпт 4 фаз (Orient → Gather → Consolidate → Prune/index) с правилами
  **«относительные даты → абсолютные»** и **«удаляй опровергнутые факты»**, кап индекса
  ~25KB. Для корпуса встреч это буквально «еженедельная сверка фактов по проектам».
- **hermes curator — инварианты жизненного цикла**: only-agent-created, **никогда не
  удалять (archive восстановим)**, pinned обходит все переходы, aux-клиент не трогает
  кэш основной сессии; отчёт прогона оператору (`_write_run_report`). Экономика
  фона: same-model → полный реплей транскрипта (тёплый кэш), чужая/дешёвая модель →
  компактный дайджест (меньше холодной записи) — `_resolve_review_runtime`.
