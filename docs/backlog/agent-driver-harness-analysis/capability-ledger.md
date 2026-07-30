# Capability ledger — реестр плоскостей харнесса

Одна строка на плоскость/возможность: где ручка, каков default, зрелость, откуда взялась,
чем доказана. Обновляется при каждом horizon-scan'е и при закрытии эпика. Правило
промоушена: **experimental → default-on только через инцидент, который плоскость
предотвратила бы, или измеренный выигрыш `eval compare`**; commit-count у референса —
сигнал приоритета, не доказательство нужности.

Тиры: **core** — без флага, выключатель был бы источником багов; **default-on** — включено,
есть escape hatch для A/B и отладки; **opt-in** — включает хост под свой профиль;
**experimental** — за флагом до доказательства; **proposed** — эпик заведён, кода нет;
**deferred** — осознанно не делаем, следим за референсами; **rejected** — с причиной.

Происхождение: `incident` (из живого сбоя — необходимость доказана болью),
`scan` (из референс-скана — гипотеза, нужен A/B), `bench` (из бенч-фикса — риск
benchmark-fitting, обязательна классификация tool/prompt/harness).

## Действующие плоскости

| Плоскость | Ручка (RunnerConfig) | Default | Tier | Происхождение | eval axis | Референс |
|---|---|---|---|---|---|---|
| Loop termination backstop | `default_max_steps` | 80 (None = unbounded) | default-on | incident (RAM в GB) | — | — |
| Budget grace (forced-final при исчерпании) | `budget_grace_enabled` | True | default-on | incident | `budget_grace` | — |
| Loop guards / doom-loop (019) | встроено | on | core | scan+incident | — | оба |
| Message protocol hygiene (018) | встроено | on | core | scan | — | оба |
| Terminal latency discipline (024) | встроено | on | core | incident («Сохраняю прогресс») | — | — |
| Status protocol / нет немой стадии (025) | контракт хоста | on | core | incident | — | — |
| Empty-response recovery ladder (016) | встроено | on | core | incident+scan | — | hermes |
| Model-aware context autoscale (017) | `TrimmingSettings.resolved_for_model` | auto (explicit хоста всегда wins) | default-on | scan | — | — |
| Partial compaction | `enable_partial_compaction` | True | default-on | — | — | — |
| PTL retry | `enable_ptl_retry` | True | default-on | — | — | — |
| Subagents (034) | `enable_subagents` + лимиты | True | default-on | scan | — | оба |
| Tool deferral / deferred-каталог (033) | `tool_defer_mode` | "auto" | default-on | scan | — | оба |
| Compaction (035, слои) | `enable_compaction` / `enable_llm_compaction` / `enable_session_memory_compaction` | False | opt-in | scan | — | оба |
| Tool-history compression (035 A, no-cache бекенды) | `enable_tool_history_compression` | False | opt-in | scan | — | hermes |
| Tool-arg truncation | `enable_tool_arg_truncation` | False | opt-in | — | `tool_arg_truncation` | — |
| Prompt-cache economics (028) | `enable_prompt_cache` | False | opt-in | scan | `prompt_cache` (только anthropic-live) | оба |
| Per-turn tool output budget (033 B) | `per_turn_output_budget_chars` | None (off) | opt-in | scan | — | hermes |
| Tool concurrency limit | `tool_concurrency_limit` | None (parallel) | opt-in | — | `tool_concurrency` | — |
| Aux-model registry / cost plane (032) | `auxiliary_models` / `aux_model_for` | пусто → provider default | opt-in | scan+incident (024: aux-модель по замеру) | — | оба |
| Subagent model routing | `subagent_model_routing` | пусто | opt-in | scan | — | — |
| Harness profiles | `harness_profiles` | пусто | opt-in | scan | — | — |
| Project memory sources | `project_memory_sources` | пусто | opt-in | scan | — | оба |
| Memory plane (021) + recall hygiene (027) + консолидация (031) | host-provided store | off без стора | opt-in | scan | — | оба |
| Python tool | `PythonToolSettings.enabled` | False | opt-in | — | — | — |
| Steering v2 (030) | диспетчер kinds | wired | core | scan+incident | — | hermes |
| Structured output headless (036) | контракт | on | core | bench (dd9a5ee extraction-флейки → harness-класс) | — | openclaude |
| Observability (010/037) | observer/middleware | on | core | scan | — | hermes |
| Suggested next questions (038) | хост-опция | opt-in | opt-in | scan | — | — |
| Answer shaping / injection hygiene (039) | промпт-блоки хоста | хост | opt-in | scan | A/B diverse-бенч | оба |
| Transcript-poisoning hygiene (043) | встроено (CoT-strip, empty-repair, scaffolding-теги, poisoned-prefix quarantine) | on | core | scan (hermes cf0c42fa0 — инцидент-класс) | — | hermes |

## Proposed (horizon-scan 040, 2026-07-29)

| Кандидат | Эпик | Суть | Референс-сигнал |
|---|---|---|---|
| Liveness/progress plane | 041 (в 040) | heartbeat тулов; aux-вызовы стримом с idle-таймаутом; stall сабагентов по progress-token | **конвергенция обоих референсов**; wall-clock watchdog у hermes отреверчен |
| Tool-call wire integrity | 042 (в 040) | не синтезировать завершённость: обрыв стрима с in-flight тулом → fail; пустой `tool_calls` → bounded re-prompt; коллизии id → ресуффикс | openclaude `2fe1e1b`, hermes `63954d508`/`474c84ed8` |
| Context-engine seam | 044 (в 040) | `select_context()` per-turn без мутации истории + `on_turn_complete()` + context breakdown | hermes `context_engine.py`; продуктовая примерка на RAG-хостах |
| Event-driven wait (park-on-event вместо поллинга) | 045 (в 040, добавлен 2026-07-30) | `wait_for_event`: подписка → checkpoint → wake с payload; liveness-бэкстоп из 041; delivery-claim из №14; дёшево тестится осью `event_wait` на DS Flash | durable_lifecycle/checkpoints готовы; hermes `async_delegation` как чертёж доставки |

Обновления существующих эпиков из 040 (фазы, не эпики): 016 (классификатор: invalid-body
до overflow, throttling, Bedrock-конверт; backend_identity 3 оси failure-scope),
019 (per-turn капы; параллельный fan-out = 1 инкремент), 033 (tiered disclosure;
blind-call schema probe), 028 (триггеры инвалидации префикса; prune-гистерезис),
018 (conformance vectors), 035 ([SKILL_PRUNED]-маркеры), 037/010 (fault containment
обзерверов). Детали и коммиты — в `epics/040-horizon-scan-2026-07-29.md`.

## Deferred (следим; №№ из сканов 020/040)

№9 clarify first-class · №10 turn lease (blueprint: hermes `SessionState`, `ab08e8fc7`) ·
№11 stale-stream breaker · №12 credentials rotation · №13 reconnect/hydration UI ·
№14 durable async-delegation · №15 cron suggestions · №16 session-slot dedup ·
№17 elicitation · №18 hook-event таксономия · №19 permission classifier-approval ·
№7(020) MoA advisors (зрелость выросла: 20 коммитов cadence/бюджеты — пересмотреть) ·
№20 crash forensics (shutdown flush, lifecycle ledger) · №21 egress credential firewall
(iron_proxy; ждёт sandboxed-тулов) · №22 relay interception plane (инвазивен, реверт у авторов).

## Rejected (с причиной, чтобы не пересматривать каждый скан)

- **NOOA / code-as-action** (nvidia-nemo/labs-OO-Agents, 2026-07-29): другая парадигма,
  противоречит принципу «right tools + right harness»; вернуться только при решении
  делать code-interpreter plane (тогда: AST-валидация, deny-lists).
- **Voice/TTS/STT-плоскости** — не наш профиль (ASR у хостов локальный по архитектуре).
- **STT provider-registry, Meet-бот в звонке, replay/branching UI** — продуктовые решения
  хостов, не движка (скан 07-23).
