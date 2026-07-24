# Экономия контекста инструментальной плоскости: deferred-каталог + spill крупных выводов

Дата создания: 2026-07-23. Статус: **DONE (A-D)** 2026-07-24.

## Итог (2026-07-24)

Инвентаризация показала: бóльшая часть эпика УЖЕ была заскаффолжена — Phase 12 H21
(`ToolManifest.should_defer/always_load/is_deferred`, омиссия из схемы, `defer_primer`,
builtin `tool_search`), H18 (per-result spill в ArtifactStore по `max_result_size_chars`),
029-A (pure-примитивы `safe_preview/persisted_output_envelope/empty_result_marker`,
экспортированы но не потреблялись). Доделаны недостающие куски:

- **A. Адаптивный порог deferral** (`tools/defer_policy.py`, hermes `should_activate`):
  `should_defer` стал КАНДИДАТОМ — defer срабатывает только когда схемы кандидатов
  пересекают `tool_defer_threshold_pct` окна (дефолт 10%, фолбэк-обрыв 20K при неизвестном
  окне), иначе force-surface инлайн (дешевле, чем round-trip через `tool_search`).
  `CapabilitySettings.tool_defer_mode` (auto/on/off) + `_threshold_pct`; врезка в
  `build.py::adaptive_defer_surface`; raw-free `tool_defer_audit` в request-метадате.
  Инертно, когда ни один тул не `should_defer` (случай MeetScript, 7-10 тулов).
- **B. Per-turn агрегатный бюджет** (`tools/executor/turn_budget.py`, hermes tier 3):
  после тул-вызовов хода, если сумма summary превышает `per_turn_output_budget_chars`,
  крупнейшие обрезаются через 029 `safe_preview` до бюджета — работает БЕЗ ArtifactStore
  (lossy+маркер), обычный хост-кейс. Плюс унификация `spill.py` preview на `safe_preview`
  (был сырой `[:limit]`, мог резать codepoint на RU/JSON). Дефолт off.
- **C. Наблюдаемость**: `tool_output_budget` роллап (`spilled_count`, `chars_saved`) в
  `context.metadata` + реестр `runtime-metadata.md`; defer-аудит в request-метадате
  (Phoenix-видим).
- **D. Хост**: гейт `CHAT_V2_TOOL_OUTPUT_BUDGET_CHARS` (dev 48000 ≈ 12k токенов / prod off)
  → `per_turn_output_budget_chars` через `run_builder`. Порог ЩЕДРЫЙ намеренно — обычный
  single-retrieval не режется (grounding цел), ловится только патологический агрегат.
  Приёмка (in-container): wiring `RunnerConfig.capabilities.per_turn_output_budget_chars=
  48000`; замер — ход 55400 симв → 27462 (сэкономлено ~6984 токена, tier-3 активировался);
  нормальный ход 6000 симв не тронут; **живой чат-ход завершился штатно (ответ 4040 симв),
  `tool_output_budget` в диагностике ОТСУТСТВУЕТ** → щедрый порог не режет норму, регресса нет.

## Урок

Перед реализацией «нового» эпика — инвентаризовать движок: 4 из 6 механизмов уже
существовали (H21/H18/029). Реальная новизна — адаптивный порог (A) + tier-3 (B) +
унификация preview. Для MeetScript (малый набор тулов, single-turn) A инертен, B —
generous backstop; главный налог `search_meeting_sources` уже ограничен движковым
триммингом (017) + хостовым пейджингом (029), поэтому агрессивный клип НЕ вводился
(риск grounding). Честный результат: движок получил общие возможности, хост — безопасный
backstop-гейт.

Дата создания: 2026-07-23 (статус исходный: proposed; номер 028→033 при консолидации — коллизия двух раунд-2 сканов).

Мотивация: два ортогональных источника токен-налога инструментальной плоскости, которые
context-budget (эпик 017) не решает — он режет ИСТОРИЮ, а не стоимость каталога тулов и
не стоимость свежих tool-выводов:
1. **Каталог тулов** — каждая схема живёт в промпте целиком с первого хода; с ростом числа
   тулов/MCP у хостов (MeetScript retrieval-тулы + memory + goal-gate) налог растёт линейно.
2. **Крупные tool-выводы** — большая retrieval-выдача либо целиком попадает в контекст, либо
   lossy-обрезается; модель не может «дочитать» отрезанное.

## Reference-first

- **hermes `tools/tool_search.py` + `toolsets.py`**: когда deferrable-тулы (MCP + non-core)
  заняли бы >threshold_pct (дефолт 10%) окна — заменяются тремя bridge-тулами
  (`tool_search`/`tool_describe`/`tool_call`), раскрытие по требованию. Core-тулы никогда
  не deferred. Каталог пересобирается каждую сборку контекста (stateless — урок регрессии
  с session-keyed каталогом). Bridge маршрутизирует через общий `handle_function_call` —
  guardrails/hooks/approval срабатывают идентично; в трейсах разворачивается в реальный тул.
- **openclaude `src/tools/ToolSearchTool/`**: та же идея; в промпте — только имена deferred-тулов,
  схема подтягивается запросом (`select:`/keyword/`+prefix`); `alwaysLoad` opt-out; каналы
  связи с пользователем не дефёрятся (контракт виден с 1-го хода).
- **hermes `tools/tool_result_storage.py` + `tool_output_limits.py` + `budget_config.py`**:
  трёхуровневый бюджет вывода: (1) per-tool pre-truncation; (2) per-result spill — вывод
  больше порога пишется файлом, в контекст идёт preview+путь (модель может дочитать
  `read_file`); (3) per-turn aggregate budget (200K) со spill'ом крупнейших непролитых.

## Эскиз фаз

A. **Deferred-каталог**: маркировка тулов deferrable, порог доли окна, bridge-тулы
   search/describe/call через существующий tool-dispatch (policy/hooks сохраняются),
   stateless-пересборка каталога, разворачивание в Phoenix-спанах.
B. **Spill tool-выводов**: контракт `tool_result_storage` (порог per-tool, preview+ссылка,
   агрегатный per-turn бюджет); интерфейс «дочитать» — либо штатный read-тул хоста, либо
   встроенный `read_spilled_result`.
C. Метаданные/наблюдаемость: сколько токенов сэкономлено deferral'ом/spill'ом за ран —
   в runtime metadata (инвентарь по дисциплине 008).
D. Приёмка на хосте MeetScript: полный бенч-свип без регрессий; замер токенов промпта
   до/после на кейсах с крупной retrieval-выдачей.

## Не в скоупе

Trimming истории (017), компакция спанов (030), каталожный routing моделей (012).
