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

## Current Open Work

### Prompt And Runtime Policy

- [x] Переписать `react_chat_tool_policy.txt` в более модульную форму:
  identity, language, tool calling, planning boundaries, research boundaries,
  deliverable boundaries, ask-question boundaries.
- [ ] Вынести volatile reminders из base prompt в runtime attachments:
  active task contract, force-final, research-required, existing checklist,
  approved plan state, steering command.
- [x] Сделать `ask_user_question` schema/prompt ближе к OpenClaude:
  1-4 short questions, explicit choices, no plan approval through questions,
  no clarification for deliverables unless blocked.
- [ ] Для deliverable задач отделить "progress checklist" от "final answer":
  checklist может существовать, но финал должен закрывать acceptance criteria.

### Scenario Harness

- [ ] Добавить Phoenix trace extractor для run id:
  LLM calls, tool calls, selected tools, effective `tool_choice`, safe runtime
  reminders, text-form tool calls, progress-only final, missing required tool
  evidence, repeated planning, extra `ask_user_question`.
- [ ] Сохранять последний failed live scenario artifact в `/tmp`: screenshot,
  transcript excerpt, trace summary JSON.
- [ ] Документировать known bad patterns: repeated planning, progress-only
  final, fake citations without tool evidence, raw JSON in chat, stuck
  clarification.
- [ ] Добить минимум 5 live Phoenix-backed scenarios на текущей default model.

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
- [ ] `plan-only`: пользователь просит только план; агент показывает checklist
  и не пишет deliverable.
- [ ] `clarification-only-when-blocked`: уточнение только для реально
  блокирующего user-owned решения.
- [ ] `subagent-synthesis` live: `agent_tool` порождает детей, parent получает
  результаты и выдает синтез.
- [ ] `steering-mid-run` live: steering применяется на ближайшей границе,
  отображается в UI и виден в trace.

### Subagents / Steering Next Layer

- [ ] Добавить Hermes-style execution blueprint для long research/writing и
  implementation chat tasks: typed phases, worker specs, required handoffs,
  verifier gate, synthesizer final answer, explicit block/retry/error policy.
  Использовать только если Phoenix traces показывают, что простой prompt+tool
  loop продолжает терять deliverable.
- [ ] Уточнить steerability semantics в стиле Hermes:
  running user input должен быть `interrupt`, `queue` или
  `steer after next tool result`, с явными UI affordances и trace labels.
- [ ] Добавить узкие policy hook points в стиле Hermes plugins:
  `pre_tool_call` veto/context hook и `post_tool_result` transform hook.
- [ ] Зафиксировать будущую compaction policy: сохранять active task contract,
  recent tail и tool-call/result integrity; back off при неэффективной
  compression.

### Quality Gate

- [ ] Для каждого исправленного failure должен быть provider-level или runtime
  unit regression.
- [ ] Перед коммитом запускать `black`, `isort`, focused `pytest`, relevant
  backend/frontend/Playwright checks.
- [ ] В конце фазы делать отдельный refactoring/code-quality pass с `pylint`:
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
- Runtime/Phoenix spans включают normalized tags:
  `task_contract.kind`, `task_contract.requires_research`,
  `tool_choice.effective`, `force_final_reason`, `continuation_reason`,
  `agent_driver.scenario`.

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
