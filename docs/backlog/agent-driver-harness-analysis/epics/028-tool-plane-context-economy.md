# Экономия контекста инструментальной плоскости: deferred-каталог + spill крупных выводов

Дата создания: 2026-07-23. Статус: **proposed** (из фиче-скана референсов 2026-07-23).

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
