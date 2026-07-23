# Зрелость контракта наблюдаемости: observer vs middleware, correlation-ID, версии схемы

Дата создания: 2026-07-23. Статус: **proposed** (из фиче-скана референсов 2026-07-23).
Дельта к эпику 010 (lifecycle hooks — плоскость есть; здесь — её контрактная зрелость).

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
