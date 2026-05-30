# OpenClaude/Hermes Improvement Plan

Дата исходного анализа: 2026-05-29.  
Последняя сводка: 2026-05-30.

Цель: улучшать `agent-driver`, беря лучшие практики из
`/home/roman/pyprojects/ML/openclaude` и
`/home/roman/pyprojects/ML/hermes-agent` в трех направлениях:

- force planning: планирование и approval только там, где оно действительно
  защищает пользователя или качество работы;
- steerability: понятное управление агентом во время выполнения;
- subagents: дочерние исполнители, mailbox, синтез и управляемая
  оркестрация без лишнего DAG-шума.

## Principles

- Движемся в духе Python Zen: простота, читаемость и проверяемое поведение
  важнее сложной механики ради механики.
- Если проблему можно надежно решить связкой "модель + prompt + небольшой
  runtime guard", сначала делаем так.
- Сложную оркестрацию добавляем только по явным сигналам из traces/tests:
  повторяемые сбои, невозможность проверить поведение, реальные требования к
  parallelism, durability, human approval или recovery.
- Если находится решение, упрощающее текущую реализацию без потери качества,
  применяем упрощение и фиксируем причину.
- Chat demo держим чистым: это витрина и проверочный стенд, а не второй
  движок. Переиспользуемое поведение уходит в `agent_driver`; в demo остаются
  FastAPI/React wiring, локальные настройки, session UI и сценарии проверки.

## Verification Loop

Рабочий цикл:

`chat scenario -> Phoenix trace -> prompt/runtime hypothesis -> focused patch -> replay`

Сценарий считается успешным только если закрыты нужные слои:

- runtime/provider unit test фиксирует конкретный сбой;
- deterministic Playwright проверяет видимое UI-поведение без зависимости от
  провайдера;
- live Playwright probe проверяет model-dependent поведение на текущем dev
  stack и сверяет `/trace-summary`.

Для planning/search/final-answer/subagent/steering изменений обязательно
добавлять либо provider/runtime regression, либо live probe criterion.

## Closed Work

### Prompt And Runtime Policy

- [x] Переписать `react_chat_tool_policy.txt` в более модульную форму:
  identity, language, tool calling, planning boundaries, research boundaries,
  deliverable boundaries, ask-question boundaries.
- [x] Вынести volatile reminders из base prompt в runtime attachments:
  active task contract, force-final, research-required, existing checklist,
  approved plan state, steering command.
- [x] Сделать `ask_user_question` schema/prompt ближе к OpenClaude:
  1-4 short questions, explicit choices, no plan approval through questions,
  no clarification for deliverables unless blocked.
- [x] Для deliverable задач отделить "progress checklist" от "final answer":
  checklist может существовать, но финал должен закрывать acceptance criteria.

### Scenario Harness

- [x] Добавить run/Phoenix trace extractor для run id:
  LLM calls, tool calls, selected tools, effective `tool_choice`, safe runtime
  reminders, text-form tool calls, progress-only final, missing required tool
  evidence, repeated planning, extra `ask_user_question`.
- [x] Сохранять последний failed live scenario artifact в `/tmp`: screenshot,
  transcript excerpt, trace summary JSON.
- [x] Документировать known bad patterns: repeated planning, progress-only
  final, fake citations without tool evidence, raw JSON in chat, stuck
  clarification.
- [x] Добить минимум 5 live Phoenix-backed scenarios на текущей default model.
  Latest suite includes `clarification-only-when-blocked`,
  `deliverable-no-replan`, `plan-only`, `plan-web-answer`, `research-report`,
  `simple-direct`, and `web-search-final`.

### Core Scenario Set

- [x] `simple-direct`: простой вопрос без planning/tools/interrupts.
- [x] `research-report`: интернет-поиск + финальный ответ с tool evidence.
- [x] `bad-tool-text-recovery`: text-form tool call не становится финалом и не
  зацикливает tools после `tool_choice=none`.
- [x] `web-search-final`: web tools -> финальный ответ.
- [x] `clarification`: clarification UI/resume работает и не ломает plan card.
- [x] `plan-approval`: deterministic approval card/resume path.
- [x] `subagent-final`: deterministic subagent synthesis UI path.
- [x] `deliverable-no-replan`: explicit deliverable не рестартует planning loop.
- [x] `plan-only`: пользователь просит только план; агент показывает checklist
  и не пишет deliverable.
- [x] `clarification-only-when-blocked`: уточнение только для реально
  блокирующего user-owned решения.
- [x] `subagent-synthesis` live: `agent_tool` порождает детей, parent получает
  результаты и выдает синтез.
- [x] `steering-mid-run` live: steering применяется на ближайшей границе,
  отображается в UI и виден в trace.

### Subagents / Steering Next Layer

- [x] Добавить Hermes-style execution blueprint для long research/writing и
  implementation chat tasks: typed phases, worker specs, required handoffs,
  verifier gate, synthesizer final answer, explicit block/retry/error policy.
  Decision: gated/deferred. Не внедряем новый graph слой, потому что текущий
  9-scenario Phoenix/live suite не показывает повторяемую потерю deliverable
  после prompt + runtime guard исправлений. Вернуться к blueprint только при
  новых trace-backed regressions.
- [x] Уточнить steerability semantics в стиле Hermes:
  running user input должен быть `interrupt`, `queue` или
  `steer after next tool result`, с явными UI affordances и trace labels.
  Current mapping: `enqueue_user_message + next` -> `queue_after_next_boundary`,
  `enqueue_user_message + now` -> `steer_at_next_boundary`, `interrupt` ->
  `interrupt_now`; live trace summary exposes `controls.semantic_routes`.
- [x] Добавить узкие policy hook points в стиле Hermes plugins:
  существующий `GovernedToolExecutor` уже поддерживает `pre_tool_use` /
  `post_tool_use`, chaining, context aggregation, timeout isolation and
  `prevent_continuation`; не добавляем второй hook механизм.
- [x] Зафиксировать compaction policy runtime-защитой: сохранять active task
  contract/runtime attachments через recent tail, сохранять leading system
  policy при partial compaction, защищать recent tail и back off при
  неэффективной compression через orchestrator circuit breaker.

### Quality Gate

- [x] Для каждого исправленного failure должен быть provider-level или runtime
  unit regression.
- [x] Перед коммитом запускать `black`, `isort`, focused `pytest`, relevant
  backend/frontend/Playwright checks.
- [x] В конце фазы делать отдельный refactoring/code-quality pass с `pylint`:
  чинить реальные naming/decomposition/typing/import/duplication проблемы, а
  не отключать warnings пачкой.

## Current Architecture Decisions

### Planning

- `todo_write` и `planning_state_update` остаются live-progress planning.
- `enter_plan_mode` / `exit_plan_mode_v2` являются modal approval planning и
  не должны использоваться для обычного research/writing в публичном chat UI.
- Force planning является runtime policy boundary для risky writes,
  side effects и subagent spawn, а не просто prompt-инструкцией.
- Простые factual/research задачи не должны получать modal plan approval.

### Deliverables And Research

- Explicit deliverable turns получают run-level contract:
  финальный ответ важнее нового plan loop.
- Research-required задачи проверяются по trace evidence, а не только по URL в
  финальном тексте.
- Pure research после первого реального `web_search`/`web_fetch` может
  включать force-final, чтобы перейти к синтезу вместо бесконечного поиска.

### OpenRouter/Qwen Compatibility

- Provider может печатать tool call как plain text или только fragment
  arguments при forced `tool_choice`.
- OpenAI-compatible streaming adapter должен:
  - парсить text-form tool calls по накопленному stream text;
  - восстанавливать forced tool call по известному tool name;
  - подавлять text-form tool execution при `tool_choice=none`.

### Observability

- Phoenix lifecycle и OpenTelemetry helpers живут в `agent_driver.observability`.
- Chat-demo использует reusable helpers, но не держит engine policy.
- `/api/chat/runs/{run_id}/trace-summary` является быстрым debug verdict:
  terminal event, LLM calls, tool names, research evidence, planning verdict,
  interrupts, progress-only final, text-form tool calls.
- Live trace summary должен уважать explicit negative constraints:
  "без поиска", "без интернета", "по памяти" отключают research-required даже
  при наличии слов "поиск/интернет" в тексте.
- Runtime/Phoenix spans включают normalized tags:
  `task_contract.kind`, `task_contract.requires_research`,
  `tool_choice.effective`, `force_final_reason`, `continuation_reason`,
  `agent_driver.scenario`.

## Known Bad Patterns

- Repeated approval planning: one run enters and exits modal planning more
  than once instead of executing the accepted plan.
- Progress-only final: the assistant ends with "I will now continue/write..." or
  an equivalent status update, but does not provide the requested answer.
- Fake research evidence: the final text cites or names sources without
  matching `web_search`/`web_fetch` trace evidence when research was required.
- Raw tool JSON in chat: provider emits textual tool-call JSON or
  `<tool_call>` markup instead of native tool calls.
- Stuck clarification: `ask_user_question` pauses the run, but resume/submit
  does not lead to a terminal answer.
- Extra clarification: `ask_user_question` is used for research or deliverable
  tasks where reasonable assumptions should be enough.

## Next Work: Subagent Autonomy And UX

Цель: в chat demo должно быть видно не только что `agent_tool` технически
работает, но и что агент разумно делегирует сложные задачи сам или по прямой
просьбе пользователя, а пользователь может заглянуть в работу дочерних агентов
без чтения сырого JSON.

### Product Behavior

- [x] Уточнить prompt/runtime policy для делегирования:
  - делегировать при явной просьбе пользователя: "поручи субагенту",
    "пусть отдельный агент проверит", "сравни несколькими исполнителями";
  - делегировать для сложных задач, где есть независимые подзадачи:
    research + verification, compare alternatives, draft + critique,
    implementation + review;
  - не делегировать для простых factual вопросов, маленьких переводов,
    коротких ответов и задач, где один проход дешевле и понятнее;
  - child prompt должен быть self-contained, bounded, с явным expected output
    и запретом выдумывать недоступные результаты.
- [x] Упростить UI capability model:
  - Web Search/Web Fetch остаются пользовательскими переключателями;
  - bounded delegation всегда доступно агенту и не выключается в chat demo;
  - модель решает, когда делегировать, по prompt policy и task complexity.
- [x] Добавить model-dependent live сценарии:
  - explicit delegation: пользователь прямо просит поручить часть работы
    субагенту, ожидаем `agent_tool`, child completed, parent synthesis;
  - autonomous delegation: сложная compare/review задача без прямого слова
    "субагент", ожидаем делегирование при обычном web-capable surface;
  - no delegation: простой вопрос при доступном `agent_tool` не должен вызывать
    `agent_tool`;
  - delegation final: parent обязан дать финальный ответ, а не только
    сообщить, что child завершился.

### Runtime / Trace Criteria

- [x] Расширить `/trace-summary` для subagent verdict:
  `delegation_requested`, `delegation_expected`, `agent_tool_used`,
  `child_runs_started`, `child_runs_completed`, `groups_joined`,
  `parent_synthesized_final`, `child_error_count`.
- [x] Добавить failure labels:
  `missed_explicit_delegation`, `unnecessary_delegation`,
  `subagent_no_final`, `child_result_not_used`, `child_prompt_not_bounded`.
- [x] В live probe считать сценарий успешным только если:
  - tool surface содержит `agent_tool` вместе с выбранными web capabilities;
  - есть `agent_tool` при explicit/autonomous delegation;
  - есть completed child/group join;
  - финальный parent answer содержит синтез child output;
  - нет повторного plan loop после join.

### Chat UI/UX

- [x] Спроектировать `SubagentPanel` вместо сырого tool JSON:
  - компактная карточка "Delegated work";
  - статусы: preparing, spawned, running, joined, failed, cancelled;
  - список child agents с role/title, short task, elapsed time, terminal state;
  - итоговый child summary в 1-3 строки;
  - кнопка/accordion "Inspect" для деталей.
- [x] Детальный просмотр должен показывать полезную работу, а не шум:
  - child prompt / task brief;
  - child final output;
  - used tools summary;
  - warnings/errors;
  - ссылки на child run id и Phoenix trace when available.
- [x] Скрыть raw `agent_tool` JSON по умолчанию:
  - raw payload оставить только в expandable debug view;
  - на основной поверхности показывать человекочитаемый lifecycle.
- [x] Продумать визуальную модель:
  - в общем чате субагенты не должны выглядеть как отдельные полноценные
    собеседники;
  - лучше формат "work packet / worker lane" внутри assistant bubble;
  - parent final answer остается главным артефактом.
- [x] Проверить accessibility:
  - статусы читаются screen reader;
  - keyboard раскрывает child details;
  - long child output не ломает scroll и composer.

### Implementation Slices

- [x] Slice 1: expose Agents preset in demo UI and docs, keep filesystem/shell
  unavailable from web surface.
- [x] Slice 2: add subagent lifecycle projection in frontend event parser:
  group started, child spawned, child completed, group joined, merge completed.
- [x] Slice 3: build deterministic `subagent-final` UI regression around
  `SubagentPanel`.
- [x] Slice 4: add live explicit/autonomous/no-delegation scenarios and Phoenix
  verdict checks.
- [x] Slice 5: prompt/runtime policy tuning from traces, keeping Python Zen
  rule: prefer model + prompt + small guard before graph machinery.
- [x] Slice 6: code quality pass with focused frontend tests, backend tests,
  Playwright screenshots, `black`, `isort`, and meaningful `pylint` fixes for
  touched Python.

## Neighbor Project Findings

### OpenClaude

- Plan mode это permission mode, не просто tool.
- Approval plans нужны для implementation, а не для research/codebase
  understanding.
- Mode instructions инжектятся как attachments, включая sparse reminders.
- `AskUserQuestion` ограничен: 1-4 questions, короткие headers, choices,
  optional "Other"; это не approval mechanism.
- `TodoWrite` это progress telemetry, требует один active step.
- Coordinator/worker prompts запрещают fake worker results и требуют
  self-contained child prompts.

### Hermes Agent

- Base persona маленькая; поведение живёт в focused runtime blocks.
- `kanban_specify` полезен как компактный `Goal / Approach / Acceptance /
  Out of scope` contract.
- `kanban_decompose` и `kanban_swarm` добавляют graph только когда реально
  нужна параллельность.
- Busy input явно делится на `interrupt`, `queue`, `steer`.
- Hooks/plugins дают тонкие extension points без встраивания всех правил в
  core loop.
- Compression protective: сохранить head/recent tail/tool-call integrity и не
  переписывать context бесконечно.

## Completed Summary

### Core Phases Closed

- Phase 1: plan artifacts, plan approval payloads, approval interrupts,
  chat-demo plan approval rendering, durable plan lifecycle tests.
- Phase 2: force-planning gate, planning hints, policy modes, remediation,
  chat-demo force-planning env/config path.
- Phase 3: steering contracts, command queue, priority/FIFO/cancellation,
  SDK facade.
- Phase 4: steering adapters, chat-demo controls, replay timeline,
  persisted steering controls.
- Phase 4a: optional Instructor spike boundary for structured extraction.
- Phase 5: native `agent_tool` spawn, idempotent child rows, continuation and
  stop controls.
- Phase 6: background subagents, mailbox, notifications, status/collection,
  abort propagation, scheduling budgets.
- Phase 7: coordinator profile, worker definitions, restricted tool surfaces,
  handoff rules, evals.
- Phase 8: child workspace/cwd isolation, bounded artifact refs, cleanup tests.

### Recent Completed Work

- Moved reusable chat-demo logic into `agent_driver`:
  - `agent_driver.context.transcript`;
  - `agent_driver.observability.message_metadata`;
  - `agent_driver.observability.phoenix`;
  - `agent_driver.observability.run_trace_summary`;
  - `agent_driver.adapters` SSE helpers;
  - `agent_driver.runtime.chat_policy`.
- Added Phoenix-backed live probe:
  `examples/chat-demo/frontend/tests/e2e/chat_live_probe.py`.
- Fixed OpenRouter/Qwen text-form tool call loops.
- Added research task contract evidence checks and force-final after research.
- Added `agent_driver.scenario` tracing for live scenario filtering.
- Committed accumulated work as:
  `ad2ecbe Improve chat runtime observability and research flow`.

## Current Verification Snapshot

Latest known good checks:

- focused runtime/observability/adapter/LLM tests passed;
- chat-demo backend tests passed with `asyncio_mode=auto`;
- `black --check` and `isort --check-only` passed on touched files;
- live `research-report` passed with `run_16b2796ea7f0`;
- live `simple-direct` passed with `run_82c3ff4d12fc`;
- after scenario-tag work, live `simple-direct` passed with
  `run_793c1c5a5614`.
- latest 6-scenario live probe passed with run ids:
  `run_939d0ad932ed`, `run_c3a4befd49bc`, `run_1fd22ffa5f16`,
  `run_50dce1de0945`, `run_8bf476954a92`, `run_f62af2122f04`.
- `subagent-synthesis` live passed with `run_0aec5f474a23`:
  `agent_tool` used, one subagent completed, group joined, final answer
  produced without unwanted research.
- `steering-mid-run` live passed with `run_7e50e962c1b8`:
  `enqueue_user_message` was queued, dequeued, applied, and final answer kept
  required web-search evidence.
- Runtime attachment refactor smoke passed:
  `simple-direct` live `run_5ab97707b513`, `research-report` live
  `run_9e79e1b12d0f`.
- Partial compaction now preserves leading system policy verbatim while
  summarizing only the middle prefix; focused compaction tests passed.
- latest 7-scenario live probe passed with run ids:
  `run_b4b5a25caf59`, `run_1305757bddea`, `run_14d441eb6cae`,
  `run_af1b198eb3fa`, `run_b662ac776ff2`, `run_bf797fbf2048`,
  `run_b8ae998bbbe2`.
- latest 9-scenario live probe passed with run ids:
  `run_accde2c30205`, `run_bd40a12ff4ba`, `run_c27db4580225`,
  `run_45d7a37c00d3`, `run_ddb9dc3a6b07`, `run_509710ff14e2`,
  `run_df503c645b13`, `run_e160a397a865`, `run_5b2c65cf69ae`.
- Subagent UX slice passed:
  - `Tools · Agents` was replaced by always-available bounded delegation;
  - Web Search/Web Fetch can coexist with `agent_tool` in the same surface;
  - deterministic `subagent-final` renders `SubagentPanel` instead of raw
    `agent_tool` JSON;
  - frontend parses subagent lifecycle events into visible child rows:
    group started, child started/completed, group joined;
  - live `subagent-synthesis` passed with `run_19fb7d84ec99`;
  - live `subagent-no-delegation-simple` passed with `run_8fd5cb28ff56`:
    no `agent_tool` for a simple prompt even when delegation is available;
  - live `subagent-explicit-delegation` passed with `run_2c1175e14afc`:
    explicit delegation used `agent_tool`, child joined, parent synthesized;
  - live `subagent-autonomous-delegation` passed with `run_23710fbfc304`:
    complex compare/review prompt used `agent_tool`, child joined, parent
    synthesized;
  - Slice 5 tuning added prompt guidance plus a tiny runtime guard:
    after successful subagent group join, the next parent LLM call is forced to
    `tool_choice=none` with `force_final_reason=subagent_group_joined`;
  - retested live subagent suite after the guard:
    `subagent-autonomous-delegation` `run_d15d8f4670b6`,
    `subagent-explicit-delegation` `run_69013e22a57d`,
    `subagent-no-delegation-simple` `run_1a6e5e73ebdf`; all stayed
    `verdict=pass`, with `planning_tool_calls=0` for subagent flows;
  - latest live subagent suite after UI/accessibility/code-quality pass:
    `subagent-autonomous-delegation` `run_0be5842a4522`,
    `subagent-explicit-delegation` `run_501813483836`,
    `subagent-no-delegation-simple` `run_3534caaa18aa`;
  - after making delegation always-on and combinable with web tools, live
    probes passed:
    `web-search-final` `run_5f112a3ef6dd`,
    `subagent-autonomous-delegation` `run_a4378cc314d2`,
    `subagent-explicit-delegation` `run_412f2fc4bd1e`,
    `subagent-no-delegation-simple` `run_83de9cb4f773`;
  - `/trace-summary` now reports subagent delegation verdict fields and the
    live run stayed `verdict=pass`.

Dev stack:

- frontend: `http://localhost:5174`;
- backend: `http://localhost:8010`;
- Phoenix: `http://localhost:6006`;
- Phoenix project: `agent-driver-chat-demo`.

## Demo Boundary Notes

Оставляем в chat-demo:

- FastAPI route composition, session title/view models, browser schemas;
- HTTP stream lifecycle registry and transcript persistence glue;
- sample workspace and per-session demo sandbox paths;
- cancellation host adapter over `RunnerConfig.cancellation_probe`;
- frontend rendering, controls and deterministic/live scenario scripts.

Выносим в `agent_driver`:

- runtime policy;
- task contracts;
- observability summaries;
- Phoenix/OpenTelemetry helpers;
- transcript/run mapping helpers;
- SSE parsing/capture helpers;
- provider/runtime compatibility guards.
