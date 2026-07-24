# Cache-safe форк-агент: субстрат побочных агентов без разрушения prefix-кэша

Дата создания: 2026-07-23. Статус: **DONE (A-D)** 2026-07-24 (из фиче-скана референсов 2026-07-23; номер 029→034 при консолидации 2026-07-23 — коллизия двух раунд-2 сканов).

## Итог (2026-07-24)

Аддитивно, БЕЗ переписывания: cache-safe примитивы для ПОЛНЫХ субагентов уже были
(`subagents/cache_safe_params.py`, `sdk/fork.py::fork_subagent`) — здесь добавлен ЛЁГКИЙ
single-call aux-субстрат для побочной LLM-работы. Полностью: `docs/aux-fork-substrate.md`.

- **A. Субстрат** — `agent_driver/llm/aux.py::aux_completion()` (4 гарантии openclaude
  forkedAgent): cache-safe (`AuxCachePrefix` → parent-префикс + `enable_prompt_cache`,
  правило «не трогать model/tools/thinking — рвёт кэш», PR #18143 45× spike закодировано),
  usage-мерж в cost-ledger с task-тегом (`merge_aux_usage`), изоляция (plain complete),
  raw-free fork-event (`aux_fork_event_payload`, epic 037).
- **B. Контракт фонового завершения** — задокументирован idle-turn инвариант (hermes
  async_delegation: доставка НОВЫМ ходом, не сплайс между tool_result и assistant →
  роль-альтернация + кэш); движок уже имеет механизмы (defer_sync + subagent-background).
  Durable completion-queue с crash-recovery — вне скоупа (низкий ROI одноходового чата,
  задокументировано). Frozen-snapshot память (hermes memory_tool) — дисциплина держится
  (recall рендерится на старте, не мутируется mid-run).
- **C. Аудит + миграция** — `structured_completion` получил `cost_ledger`/`task` → мержит
  usage (закрыт гэп: memory-extraction/структурные эмиты больше не теряются); компакция
  через `aux_model_for("compaction")` (закрыт гэп: читала `auxiliary_model` напрямую, минуя
  032-реестр). Memory-extraction (фон, post-receipt) — ledger-мерж отложен с причиной.
- **D. Приёмка** — 6 тестов субстрата (cache-prefix, usage-мерж, raw-free event, no-op-ы);
  свод llm/memory/context/observability зелёный (2 pre-existing phase6-фейла подтверждены
  на дереве без 034); MeetScript-свип без регрессий (аддитивно).

Мотивация: у движка накапливаются «побочные» LLM-работы (суммаризация, извлечение фактов
памяти, будущая компакция спанов, титулы сессий, рекапы) — сейчас каждая делается ad-hoc
вызовом. Нет единого субстрата, который (а) гарантирует prompt-cache hit родителя,
(б) не вмешивается в основную петлю, (в) корректно учитывает usage и пишет свой транскрипт.

## Reference-first

- **openclaude `src/utils/forkedAgent.ts`** — эталон: форк-агент делит cache-critical
  параметры с родителем (гарантия cache hit), полный usage-tracking, изолированное
  мутабельное состояние, отдельный abort-controller, sidechain-транскрипт. На нём стоят
  contextCollapse, SessionMemory, autoDream, speculation.
- **hermes `tools/async_delegation.py`** — контракт возврата фоновой работы: completion-event
  кладётся в общую очередь и всплывает как НОВЫЙ ход, когда агент idle — сознательно НЕ
  сплайсится между tool_result и assistant-сообщением (целостность ролевой альтернации +
  prefix-кэш). Payload самодостаточный (goal/context/result) — родитель мог уйти дальше.
- **hermes `tools/memory_tool.py`** — дисциплина frozen snapshot: память инжектится в system
  prompt снапшотом на старте, mid-session записи durable на диск, но промпт НЕ мутируется
  до следующего старта — prefix-кэш живёт всю сессию.
- **hermes `agent/title_generator.py`** — образец потребителя: асинхронная генерация титула
  после первого обмена, вне критического пути ответа.
- **hermes `agent/auxiliary_client.py`** — единый резолвер бэкенда для side-вызовов
  с fallback-цепочкой и отдельным accounting (смежно с 012; здесь — как точка, куда
  форк-агент ходит за моделью).

## Эскиз фаз

A. **Субстрат**: `fork_agent()` в runtime — наследование cache-critical конфигурации,
   изоляция состояния, отдельный abort, sidechain-журнал, usage в общий учёт с пометкой.
B. **Контракт фонового завершения**: очередь completion-событий, доставка как idle-turn
   (не сплайс в текущий ход); терминальная дисциплина 024 применяется к форкам.
C. **Аудит существующих side-вызовов** (memory extraction, goal-gate грейдер хоста,
   суммаризации) → перевод на субстрат; проверка «frozen snapshot»-дисциплины там, где
   движок инжектит память в промпт.
D. Приёмка: cache-hit-rate родителя не деградирует при активных форках (метрика в usage);
   бенч-свип MeetScript без регрессий латентности.

## Не в скоупе

Мульти-агентная оркестрация/teams (отдельный горизонт), компакция спанов (030 — потребитель
этого субстрата), routing моделей (012).
