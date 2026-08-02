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
| Liveness: idle-bounded side/aux calls (041) | `aux_idle_timeout_seconds` | None (off) | opt-in | scan (конвергенция обоих референсов) | — | оба |
| Tool-call wire integrity (042) | встроено (id-дедуп, empty-tool-calls re-prompt, truncation-gate) | on | core | scan | — | оба |
| Context-engine seam (044) | встроено (context_breakdown + fail-open select-context); `before_llm_request`/`on_run_completed` — механика | on | core | scan | — | hermes |
| Event-driven wait / park-on-event (045) | builtin-тул `wait_for_event` (opt-in вызовом модели); подписка всегда bounded | opt-in | scan (запрос пользователя) | `event_wait` (предложена, не реализована) | hermes async_delegation (чертёж) |
| SQLite durability (046 #1) | `open_sqlite_connection` (единый opener) | on | core | scan (прод-инцидент hermes) | — | hermes |
| JSON structure-preserving truncation | `shrink_json_tool_content` (2 сайта компакции) | on | core | broad audit (баг-опыт пользователя) | — | hermes `_truncate_tool_call_args_json` |

## Proposed

Скан 040 полностью закрыт (эпики 041-045 + фазы 016/019/033/037; 028/035 N/A, 018 отложен).

**Скан 046 (2026-07-30, дельта слабая — hermes +449 ~90% шум, openclaude +7):**

| Кандидат | Эпик | Суть | Референс-сигнал |
|---|---|---|---|
| SQLite durability hardening | 046 #1 ✅ DONE | `open_sqlite_connection`: WAL + busy_timeout(30s) + WAL-fallback детекция; 3 сайта проведены | hermes `8da8a7887`/`f50d80e8e` — прод-инцидент (10.8GB, 9 процессов) |
| Amortized per-turn micro-compaction | 047 (proposed) | пост-ходовое сворачивание старейшего хода в накопительное summary; НО ломает кэш-префикс каждый ход → default-off, A/B occupancy-vs-cache | hermes `186cad02f` (~14 коммитов) |
| Fail-loud name collisions + provenance | 046 #3 (proposed) | коллизия имён помечает ОБА входа, не тихий precedence; provenance-заголовок | hermes `78598d091` |

**Upstream Goal — PentestLens embedding-readiness (2026-08-02, эпики 048–055):** downstream-продукт
встраивает движок; требования в `upstream-requirements.md`. Зонтик
`epics/048-pentestlens-embedding-readiness-goal.md` + 7 под-эпиков (по 5 code-инспекциям базы `ed6a2a3`):

| U | Под-эпик | Суть gap'а | Оценка |
|---|---|---|---|
| U1 | 049 embedding-facade | **Phase A + C DONE** — facade-довод (store/command/hook/stream) + exact export-снапшот (sdk/runtime/tools) + deprecation-policy в embedding.md; `__version__` в U7; свип 2948. Осталось D/E (переписать cookbook-examples с internal-путей + e2e; единый `embedding`-namespace — опц.) | средне |
| U2 | 050 gate-identity+provenance | **A/B/D DONE** (identity в контекст + `GateProvenance` под reserved `_ad_` + `ensure_bounded_json_metadata` fail-closed + ask-path→interrupt; свип 2889); осталось C/E (сквозные проекции, унификация `interrupt_id`, retry/timeout/abort-матрица) | крупно |
| U3 | 051 atomic-approval | **контракт-срез + B/C/D-ядро DONE** — `ApprovalConsumptionStore` (in-mem+SQLite CAS-ledger, facade-экспорт, опц. `RunnerConfig(approval_store=)`): exactly-once consume, доказан один side-effect под 16-поточной гонкой + 2 concurrent resume + restart; **+ F2 prior-result replay** (`result_payload` в ledger + `RunnerConfig(replay_prior_result=)` → дубль возвращает прежний output verbatim; свип 2957). Осталось: монотонный checkpoint-revision (F3, design-note) | **крупнейшее** |
| U4 | 052 durable-stop | **C(hook) + A/D + B(границы) + `CANCELLATION_FAILED` DONE** — `AbortLifecycleStore` (restart-queryable lifecycle, facade), `_finalize_abort_lifecycle`, timeout-под-abort→`CANCELLATION_FAILED`; свип 2948. + **F1 attempt-epoch + fencing enforce** (штамп result-envelope + drop-stale-straggler + `RESULT_FENCED`; свип 2955). Осталось: **mid-in-flight-LLM-await abort** (terminal-mapping риск), deadline на токене | крупно |
| U5 | 053 plan-integrity | **ядро A/B/C + enforcement + PlanArtifactStore-wiring DONE** — harness-hash + EDIT-re-hash + `detect_plan_revision` + policy-binding + `required_plan_hash` (гейт DENY на ревизии) + durable `PlanArtifact` на approve/reject (`RunnerConfig(plan_artifact_store=)`); свип 2948. Осталось: binding через trace-проекцию (мелко) | средне |
| U6 | 054 gateway-truthfulness | **DONE (Option 2)** — `durable_parked_runs=False` + `require_durable_recovery()` fail-fast + docstring на прямой путь; свип 2907 | мелко |
| U7 | 055 version-release | **`__version__`+compat-нота DONE** (single-source из metadata, guard-тест, additive/persisted-compat); осталось release-действие (version-bump/wheel/SHA-256/handoff-doc, в конце Goal) | мелко |

Порядок: U2→U3→U4 вместе (общий durable-store+identity), U5 цепляется, U1 финализируется последним,
U6/U7 — рано и независимо.

Watching: credential-pool generation-guard (openclaude, если добавим cooldown-pooling),
isolation soft-fallback degradation-contract (openclaude, worktree-adjacent), live-steer
running delegate (hermes, продуктовое), MCP shutdown-drain (hermes, низкая генеральность).
Детали — `epics/046-horizon-scan-2026-07-30.md`.

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
