# Зрелость контракта наблюдаемости: observer vs middleware, correlation-ID, версии схемы

Дата создания: 2026-07-23. Статус: **DONE (A-E)** 2026-07-23 (из фиче-скана референсов 2026-07-23; номер 032→037 при консолидации 2026-07-23 — коллизия двух раунд-2 сканов).
Дельта к эпику 010 (lifecycle hooks — плоскость есть; здесь — её контрактная зрелость).

## Итог (2026-07-23)

Формализация hook-плоскости (010) контрактной зрелостью из hermes — **аддитивно**, БЕЗ
переписывания типизированного `RunLifecycleHook` Protocol в string-keyed plugin-manager
(это был бы churn; наш выбор — матурировать контракт, не заменять плоскость).

- **A. Разделение.** `agent_driver/contracts/observability.py`: версии `OBSERVER_SCHEMA_VERSION`
  = `agent_driver.observer.v1` / `MIDDLEWARE_SCHEMA_VERSION` = `.middleware.v1`; классификация 7
  методов Protocol — observer (`on_run_start`/`after_llm_response`/`on_error`/`on_run_completed`,
  read-only, fail-open) vs middleware (`before_llm_request`/`on_finalize`/`on_tool_evidence`,
  behavior-changing); `hook_method_role()`, `describe_observability_contract()`. Lock-тест
  `test_every_hook_method_is_classified_exactly_once` (обе стороны) → новый метод форсирует
  observer-vs-middleware решение. Существующие хуки **классифицируются, НЕ мигрируют** (0
  поведенческих изменений).
- **B. Correlation.** `deterministic_trace_id(run_id, attempt_id)` — единый seed; и emit-путь
  (`SingleAgentStepMixin._emit` штампует его на КАЖДОЕ событие), и `trace_builder` выводят
  trace_id из него → событие и его спан имеют один id ПО ПОСТРОЕНИЮ (span↔run без clock-skew).
  `EventSpec.trace_id/severity/redaction`; `correlation_ids()` бандл. Live: 8/8 событий рана с
  одним trace_id.
- **C. Санитизация + has_hook.** `observability/redaction.py::sanitize_observer_payload` (порт
  hermes `_hook_jsonable`/`_is_sensitive_hook_key`: bounds depth 8/string 8000/seq 200 +
  secret-key маскировка `<redacted>` exact+suffix) → `RedactionInfo`; **`RuntimeEvent.redaction`
  ожил** (был dead — недостижим через `new_runtime_event`; добавлен в `RuntimeEventOptions`).
  `has_hook(hooks, method)` — cheap-path гейт (override-detection vs `BaseRunLifecycleHook`),
  композится с `_emit_if_slow` и no-op-tracing.
- **D. Версионирование.** Константы + `describe_observability_contract()` + `docs/observability-
  contract.md`; новых `context.metadata`-ключей НЕТ (correlation на событии, не в metadata) →
  инвентарь 008 не задет.
- **E. Приёмка.** 20 новых тестов; zero-overhead при пустых подписчиках (`has_hook`=False, payload
  не строится); свод contracts/observability/runtime/llm зелёный (3 pre-existing фейла
  подтверждены на дереве без 037). **Live MeetScript:** чат работает без регрессий (ход 18.1с,
  231 событие, grounded, 3 подсказки); Phoenix-трейс цел. Готча приёмки: воркер серийный (~15с/ран)
  — бэклог тестовых энкью маскировался под «зависание» (раны в очереди = status running), пока
  файловый лог `[JOBWORKER] processing/finished` + Redis (status completed, 250 событий) не показали,
  что раны завершаются; ложная тревога, не дефект.

## Не в скоупе (реализовано как задумано)

Полный string-keyed plugin-manager (наш типизированный Protocol остаётся); хостовая
пропагация trace_id в MeetScript-конверты событий (SSE коррелирует по run_id; Phoenix-корреляция
на уровне движка) — опциональное хост-улучшение, не часть движкового контракта.

Мотивация: наша hook-плоскость (010) не различает формально «наблюдение» и «изменение
поведения»; correlation-модель спанов Phoenix собрана хостом ad-hoc; payload'ы хуков не
имеют контракта санитизации (важно для PII-границы MeetScript: что уходит в трейсинг-стор);
схема событий не версионирована (хосты подписаны на неявный формат).

## Reference-first

- **hermes `docs/observability/README.md` + `plugins/observability/{langfuse,nemo_relay}/`** —
  observer hooks: read-only, fail-open, стабильный набор lifecycle-событий
  (session/turn/api-request/tool/approval/subagent) с корреляционными ID
  (`session_id`/`task_id`/`turn_id`/`api_request_id`/`tool_call_id`/`parent↔child`),
  bounded+redacted payload'ы, тайминги/статусы. Дорогая сборка payload'а гейтится за
  `has_hook(...)` — неинструментированный путь дёшев. Версия схемы `hermes.observer.v1`.
- **hermes `docs/middleware/README.md`** — отдельный behavior-changing контракт:
  `llm_request`/`tool_request` (переписать kwargs до исполнения), `llm_execution`/
  `tool_execution` (обернуть сам вызов, сохранив retry/streaming/interrupt/hooks).
  `hermes.middleware.v1`.
- **hermes `agent/redact.py`** — редакция секретов до попадания в логи/хуки (vendor-префиксы
  ключей + имена sensitive-параметров; длинные токены — первые 6/последние 4).
- Наш якорь: `docs/phoenix-openinference-trace-contract.md` — контракт спанов уже есть,
  но собирается без формальной observer-плоскости.

## Эскиз фаз

A. **Разделение контрактов**: типизированные observer-hooks (read-only, fail-open) отдельно
   от middleware (rewrite/wrap); существующие lifecycle-хуки 010 классифицируются и
   мигрируют без поведенческих изменений.
B. **Correlation-модель**: единые ID (run/turn/api_attempt/tool_call/parent-child для
   форков из 029) прошиты в события и Phoenix-спаны — матчинг спан↔прогон перестаёт
   зависеть от clock-skew эвристик.
C. **Санитизация payload'ов**: bounded+redacted по контракту, точка встраивания хостовой
   PII-редакции ДО экспорта; `has_hook`-гейт дорогой сборки.
D. **Версионирование**: `agent_driver.observer.v1` / `.middleware.v1`; документация ключей
   в инвентаре runtime metadata (дисциплина 008).
E. Приёмка: Phoenix-трейс MeetScript собирается через observer-плоскость без потери полей;
   замер оверхеда с выключенными подписчиками ≈ 0.

## Не в скоупе

Каталог провайдеров/routing (012), UI-поверхности статуса (025 — потребитель, не часть).
