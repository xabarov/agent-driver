# OpenClaude Improvement Plan: Force Planning, Steerability, Subagents

Дата анализа: 2026-05-29.

Цель: усилить `agent-driver`, взяв лучшие архитектурные идеи из
`/home/roman/pyprojects/ML/openclaude` в трех направлениях:

- force planning: обязательное планирование до выполнения рискованных или
  многошаговых задач;
- steerability: возможность направлять модель во время диалога и выполнения;
- subagents: управляемые дочерние агенты, команды и параллельная оркестрация.

## Development Philosophy

Работу над движком ведем в духе Python Zen: простота и читаемость важнее
сложной механики ради механики. Если проблему можно надежно решить элегантной
связкой "модель + промпт + небольшой runtime guard", выбираем этот путь до
построения сложных DAG, workflow engine или многоуровневых state machines.

Сложную оркестрацию добавляем только по явным сигналам: повторяемые сбои в
трассах, невозможность проверить поведение тестами, реальные требования к
параллельности, durability, human approval или восстановлению после ошибок.
Если в ходе работы находится решение, которое упрощает уже добавленную
механику без потери наблюдаемого качества, применяем упрощение и фиксируем
причину в плане.

Chat demo держим чистым: это витрина и проверочный стенд, а не второй движок.
Все полезное, переиспользуемое или влияющее на поведение агента выносим в
`agent_driver`; в demo оставляем только FastAPI/React wiring, локальные
настройки, session UI и сценарии проверки.

## Verification Methodology

Сценарий считается успешным только если закрыты три слоя проверки:

- runtime contract/unit test: фиксирует конкретный сбой модели или runtime
  transition, например progress-only ответ `Теперь работаю над следующим
  шагом...` должен автоматически продолжаться до финального ответа;
- deterministic Playwright scenario: через настоящий React UI проверяет 5-7
  пользовательских действий и видимые критерии успеха без зависимости от
  провайдера;
- live chat probe when behavior is model-dependent: запускается на текущем
  dev stack, ждёт исчезновения `Stop generation` и `Writing`, затем проверяет,
  что ответ не оборвался на плане/следующем шаге, tool cards не показывают raw
  JSON по умолчанию, а финальный текст соответствует задаче.

Текущий вывод: старые UI-smoke тесты были полезны, но недостаточны для
проверки живой модели. Поэтому после каждого исправления planning/search/final
answer добавляем либо provider-level regression, либо live probe criterion.

## Quality Improvement Plan, 2026-05-30

Цель следующего цикла: повысить качество поведения модели не через усложнение
runtime как самоцель, а через короткую петлю наблюдаемости:

`chat scenario -> Phoenix trace -> prompt/runtime hypothesis -> focused patch -> replay`.

### Q1. Prompt Research From Neighbor Projects

- [ ] Провести focused read system-prompt и tool-prompt зон OpenClaude
  (частично выполнено, findings ниже):
  - `src/tools/EnterPlanModeTool/*`
  - `src/tools/ExitPlanModeTool/*`
  - `src/tools/AskUserQuestionTool/*`
  - `src/tools/TodoWriteTool/*`
  - `src/utils/messages.ts`
  - `src/utils/attachments.ts`
  - `src/hooks/toolPermission/*`
  - `src/utils/model/agent.ts`
- [ ] Провести focused read Hermes Agent prompt/runtime зон
  (частично выполнено, findings ниже):
  - `agent/prompt_builder.py`
  - `agent/context_compressor.py`
  - `agent/skill_commands.py`
  - `hermes_cli/default_soul.py`
  - `hermes_cli/profiles.py`
  - `hermes_cli/kanban_specify.py`
  - `hermes_cli/kanban_decompose.py`
  - `hermes_cli/kanban_swarm.py`
  - `hermes_cli/hooks.py`
  - `hermes_cli/gateway.py`
- [ ] Составить короткую матрицу переносимых практик:
  - какие правила должны жить в base system prompt;
  - какие правила должны быть runtime reminders/attachments;
  - какие должны быть tool schema descriptions;
  - какие должны быть guards/evals, а не промпты.
- [ ] Перенести только практики с явной пользой для наших сценариев. Не
  копировать большие промпты целиком.

Текущие findings:

- OpenClaude полезен строгими tool prompt boundaries: `AskUserQuestion` только
  для реально блокирующих user-owned решений, не для approval плана;
  `TodoWrite` держит один active step и закрывает задачи только после проверки;
  `ExitPlanMode` отделяет готовый approval plan от обычного research/progress.
- Hermes Agent полезен простотой: явный active task, resolved/pending
  questions в compressed context, маленький specify contract
  `Goal/Approach/Acceptance/Out of scope`, verifier/synthesizer как отдельный
  gate только там, где параллельность реально нужна.
- Для `agent-driver` это означает: сначала prompts + task contract + runtime
  guards + trace verdict, и только при повторных trace-сбоях усложнять
  orchestration.

### Q2. Phoenix-Led Scenario Harness

- [x] Добавить первый backend debug endpoint для trace verdict без ручного
  клика по Phoenix:
  - `GET /api/chat/runs/{run_id}/trace-summary`;
  - summary считает terminal event, LLM calls, tool names, research evidence,
    planning verdict, interrupts, progress-only final и plain-text tool calls.
- [x] Добавить живой runner сценариев поверх chat-demo, который сохраняет:
  - `session_id`, `run_id`, user prompt, final visible transcript;
  - наличие/отсутствие `Writing` и `Stop generation`;
  - видимые tool cards и их статусы;
  - Phoenix trace URL или trace id;
  - verdict по сценарию.
  Первый вариант: `examples/chat-demo/frontend/tests/e2e/chat_live_probe.py`
  сохраняет `run_id`, prompt, screenshot и trace-summary JSON; Phoenix URL/trace
  id остается следующим шагом после добавления trace id в spans/API.
- [ ] Добавить Phoenix trace extractor для run id:
  - LLM call count, tool call count, selected tool names;
  - `tool_choice` на каждом LLM call;
  - system/runtime reminders в безопасно усеченном виде;
  - признаки failure: text-form tool call, progress-only final, missing required
    tool evidence, повторный план, лишний `ask_user_question`.
- [x] Для каждого scenario хранить acceptance criteria в коде теста, а не
  только в человеческом описании.
- [x] Считать scenario успешным только если UI verdict и trace verdict оба
  зелёные.
  `chat_concepts_smoke.py` теперь дергает `/trace-summary` из браузера, а
  `chat_live_probe.py` применяет trace acceptance criteria к живому run.

### Q3. Core Scenario Set

- [ ] `simple-direct`: простой вопрос без планирования, tools и interrupts.
- [ ] `plan-only`: пользователь просит только план; агент показывает checklist
  и не пытается писать deliverable.
- [ ] `research-report`: пользователь просит поиск в интернете + реферат; агент
  обязан использовать `web_search`/`web_fetch`, не завершаться на плане и выдать
  финальный текст.
- [ ] `deliverable-no-replan`: после уже существующего плана пользователь
  просит готовый текст; агент не рестартует planning loop.
- [ ] `clarification-only-when-blocked`: уточняющий вопрос появляется только
  для действительно блокирующего user-owned решения, submit работает.
- [ ] `subagent-synthesis`: `agent_tool` порождает дочерних исполнителей, parent
  получает результаты и выдает синтез, а не только статус.
- [ ] `steering-mid-run`: user steering применяется на ближайшей границе,
  отображается в UI и виден в trace.
- [ ] `bad-tool-text-recovery`: модель печатает JSON/`</tool_call>` текстом;
  runtime не принимает это как финал, UI не показывает мусор.

### Q4. Prompt/Runtime Improvements To Try First

- [ ] Переписать `react_chat_tool_policy.txt` в более модульную форму:
  identity, language, tool calling, planning boundaries, research boundaries,
  deliverable boundaries, ask-question boundaries.
- [ ] Вынести volatile reminders из base prompt в runtime attachments:
  active task contract, force-final, research-required, existing checklist,
  approved plan state, steering command.
- [ ] Сделать `ask_user_question` schema/prompt ближе к OpenClaude:
  1-4 short questions, explicit choices, no plan approval through questions,
  no clarification for deliverables unless blocked.
- [x] Усилить tool-calling contract для OpenRouter/Qwen:
  never print JSON tool calls; if a tool is required, use native tool call; if
  provider ignores forced `tool_choice`, runtime must detect and retry once with
  a stronger reminder.
- [x] Для research-required задач проверять не только наличие URL в ответе, а
  наличие реального tool evidence в trace.
- [ ] Для deliverable задач отделить "progress checklist" от "final answer":
  checklist может существовать, но финал должен закрывать acceptance criteria.

### Q5. Observability And Debuggability

- [x] Добавить компактный trace summary в chat-demo dev UI или debug endpoint:
  run id, model, LLM calls, tools, interrupts, continuation nudges, forced-final
  reasons.
- [x] Перенести Phoenix/OpenTelemetry lifecycle из chat-demo в
  `agent_driver.observability`, чтобы demo переиспользовал библиотечный слой:
  `PhoenixTracingConfig`, `setup_phoenix_tracing`, `start_otel_span`,
  `trace_otel_event_span`.
- [x] Перенести reusable metadata/stream helpers из chat-demo в `agent_driver`:
  - `agent_driver.observability.aggregate_message_metadata_from_events` и
    `merge_message_metadata` вместо demo-local token/planning aggregation;
  - `agent_driver.observability.summarize_run_trace` вместо demo-local quality
    verdict logic для `/trace-summary`;
  - `agent_driver.adapters.AssistantTextCapture` и `parse_sse_data_payload`
    вместо ручного SSE parsing / assistant text capture в demo relay.
- [x] Перенести reusable transcript/run mapping helpers из chat-demo в
  `agent_driver.context.transcript`: `transcript_to_messages`,
  `truncate_transcript_for_retry`, `turn_text_for_run`,
  `filter_client_requests_for_runs`.
- [x] Перенести reusable chat policy из demo endpoint в
  `agent_driver.runtime.chat_policy`: task contract metadata, deliverable
  no-replan deny list, force-planning metadata и initial web-search choice для
  research-required задач.
- [x] Укрепить OpenAI-compatible provider для OpenRouter/Qwen:
  - text-form tool calls, разбитые по streaming chunks, парсятся по
    накопленному тексту;
  - если forced `tool_choice` возвращает только текстовый фрагмент arguments,
    runtime восстанавливает `ToolCall` по известному имени tool;
  - если runtime уже выставил `tool_choice=none`, text-form tool calls не
    превращаются обратно в tool execution loop.
- [x] Добавить runtime guard для pure research contracts: после первого
  реального `web_search`/`web_fetch` включается force-final режим, чтобы модель
  переходила к синтезу, а не бесконечно продолжала web tools.
- [ ] В Phoenix spans добавить normalized tags:
  `agent_driver.scenario`, `task_contract.kind`,
  `task_contract.requires_research`, `force_final_reason`,
  `continuation_reason`, `tool_choice_effective`.
  Частично сделано: chat-demo run span уже пишет `task_contract.kind`,
  `task_contract.requires_research` и `tool_choice.effective`; следующие
  теги требуют протянуть причины force-final/continuation из runtime events.

### Demo Boundary Notes

Что после ревизии сознательно оставляем в chat-demo:

- FastAPI route composition, session title/view models and browser-specific
  schemas: это host/UI contract, не engine API.
- `sse_relay.ensure_run_task` registry: связан с lifecycle HTTP stream,
  reconnect semantics, local `on_finish` transcript persistence и
  cancellation context; в `agent_driver.adapters` вынесены только чистые
  helpers.
- `workspace.py` sample project and per-session web-demo sandbox paths:
  полезно для demo, но не должно становиться глобальной политикой движка.
- `run_cancel.py` пока остается host adapter поверх `RunnerConfig`
  `cancellation_probe`; в движке уже есть более общий `RunAbortHandle`, а
  отдельная миграция cancellation в chat-demo должна быть небольшим
  последующим refactor, не частью текущего observability выноса.
- Chat task-policy decisions теперь не живут в route handler: demo только
  подставляет settings/body, а reusable policy находится в engine runtime.
- [ ] Сохранять последний failed live scenario artifact в `/tmp`:
  screenshot, transcript excerpt, trace summary JSON.
- [ ] Документировать known bad patterns: repeated planning, progress-only
  final, fake citations without tool evidence, raw JSON in chat, stuck
  clarification.

### Q6. Phase Exit Criteria

- [x] Все deterministic Playwright scenarios зелёные.
  Проверено 2026-05-30: все сценарии `chat_concepts_smoke.py` прошли на
  `localhost:5174`.
- [ ] Минимум 5 live Phoenix-backed scenarios зелёные на текущем default model.
- [ ] Для каждого исправленного failure есть provider-level или runtime unit
  regression.
- [ ] `black`, `isort`, focused `pytest`, frontend Vitest и relevant Playwright
  smoke пройдены.
- [ ] Перед коммитом отдельно проверить git status и не трогать unrelated
  generated/delete артефакты.

## Executive Summary

`agent-driver` уже содержит сильный фундамент: durable runtime, typed events,
HITL interrupts, governed tools, `todo_write`, planning state, SSE projection,
SDK facade и sync subagent execution. Поэтому задача не в переносе кода из
OpenClaude, а в переносе зрелых продуктовых паттернов:

1. Разделить два вида планирования:
   - живой checklist (`todo_write`) для прогресса внутри turn/run;
   - approval plan artifact для режима "сначала план, потом действие".
2. Сделать steerability отдельным control-plane runtime subsystem, а не набором
   ad hoc metadata keys.
3. Довести subagents от request-envelope/sync-групп до адресуемых workers с
   mailbox, stop/continue, план-approval для детей и background execution.

## Phase Backlog

| Phase | Status | Focus | First deliverable |
| ----- | ------ | ----- | ----------------- |
| 1 | done | Plan artifact + approval foundation | `PlanArtifact`, approval payload, artifact store |
| 2 | done | Force planning policy engine | Runtime gate for risky tools/subagent spawn |
| 3 | done | Steering contracts and queue | `ControlRequest` + durable command queue |
| 4 | done | Steering adapters | SSE/SDK/chat-demo control APIs |
| 4a | done | Optional Instructor spike | Pydantic-validated structured extraction adapter |
| 5 | done | Native subagent spawn | `agent_tool` schedules durable child runs |
| 6 | done | Background subagents + mailbox | async children, task notifications, mailbox |
| 7 | done | Coordinator profile | coordinator/worker prompts and evals |
| 8 | done | Isolation and advanced backends | worktree/cwd isolation, artifact handoff |

Current completed slices:

- Added public contracts for durable plan artifacts and plan approval payloads.
- Added process-local plan artifact store and lifecycle helpers.
- Added focused contract/store tests.
- Wired `exit_plan_mode_v2` with plan content into `plan_approval_required`
  HITL interrupts; approve resumes through the existing interrupt path.
- Added chat-demo plan-specific interrupt rendering: plan content, path/hash,
  and plan-content edit submission are visible in `InterruptCard`.
- Added `enter_plan_mode` and `exit_plan_mode_v2` to the built-in `planning`
  tool pack so chat-demo safe/dev presets can exercise approval mode.
- Added chat-demo dev compose with backend/frontend hot reload, repo `.env`
  passthrough, Docker volumes for Python/Node dependencies, and optional
  `CHAT_DEMO_FAKE_SCENARIO=plan_approval` smoke path.
- Added fake plan-approval backend scenario and backend test covering
  stream -> `interrupt_requested` -> fetch interrupt -> approve resume.
- Fixed chat-demo SSE tailing so `interrupt_requested` terminates the current
  stream cleanly, and fixed the frontend session reload path so the pending
  approval card is not overwritten.
- Started Phase 2 with a metadata-driven force-planning policy gate:
  `tool_policy.metadata.force_planning.enabled=true` blocks gated side-effect
  tools until `approved_plan_id` or `approved=true` is present, while planning
  tools remain exempt.
- Wired plan approval resume into the force-planning gate: approving/editing a
  `plan_approval_required` interrupt now stores approved plan metadata and
  updates the run's tool policy metadata so later side-effect tools in the same
  run can proceed.
- Added chat-demo force-planning request plumbing: `/chat/messages` accepts
  `force_planning`, and `CHAT_DEMO_FORCE_PLANNING` can set the backend default.
  The public web UI now keeps planning always-on and hides raw planning handles.
- Added deterministic `CHAT_DEMO_FAKE_SCENARIO=force_planning_block` path:
  the fake provider attempts a gated `file_write`, force planning denies it
  before execution, and run replay renders a visible `denied` tool card.
- Product decision for chat-demo: public web UI exposes web search/fetch only;
  filesystem/shell controls and raw planning handles are hidden. Planning stays
  always-on inside the agent/runtime and is surfaced through outcomes such as
  plan approvals, planning snapshots, and policy-denied replay cards.
- Added model-facing remediation for force-planning denials: a blocked
  side-effecting tool now carries structured guidance to enter plan mode and
  call `exit_plan_mode_v2` before retrying.
- Added Claude Code-like adaptive planning guidance to the chat policy:
  use plan mode proactively for non-trivial implementation, skip it for simple
  direct tasks and research-only work, and follow `force_planning_required`
  remediation when the runtime gate blocks a side-effecting tool.
- Exposed `content`, `plan`, `plan_id`, and `path` in the model-visible
  `exit_plan_mode_v2` schema so the tool contract matches the existing handler
  and plan approval interrupts can be requested by native tool call.
- Added deterministic `planning_hint` classification with English/Russian rule
  tests. Chat-demo now attaches the hint to `tool_policy.metadata`, and the
  React chat system prompt surfaces it only when planning is suggested or
  required.
- Added evaluator support for configurable force-planning modes:
  `off`, `prompt_only`, `required_for_writes`, `required_for_risky_tools`, and
  `always_for_multistep`. The existing `enabled=true` behavior remains
  compatible and maps to write/external side-effect gating.
- Chat-demo backend now accepts `CHAT_DEMO_FORCE_PLANNING_MODE` /
  `CHAT_DEMO_PLANNING_MODE` and passes the chosen mode into
  `tool_policy.metadata.force_planning` when force planning is enabled.
- Added typed `PlanningPolicyInput` / `PlanningPolicyMode` contracts and
  switched force-planning evaluator normalization away from ad hoc dictionaries
  while keeping legacy metadata compatibility.
- Extended `planning_hint` to planned tool batches. Runtime can now derive a
  required hint from side-effecting tools, `agent_tool`, or expected step count;
  hosts can opt into enforcement with `planning_hint_enforce=true`.
- Re-ran a current force-planning browser smoke against chat-demo with fake
  provider. Backend replay includes the denied `file_write` and remediation;
  the related live UI regression was folded into the chat demo design baseline
  and smoke checks in `docs/chat-demo.md`.
- Started Phase 3 with transport-neutral steering contracts and an in-memory
  command queue store covering priority ordering, FIFO, cancellation, applied
  state, dedupe keys, and route filters.
- Added SQLite command queue persistence with the same behavior contract as the
  in-memory queue and a re-instantiation persistence test.
- Added SDK steering facade methods:
  `control`, `enqueue`, `set_model`, `set_permission_mode`, and
  `cancel_queued_message`, backed by the command queue store.
- Wired command queue draining into the runtime LLM step boundary for `now` and
  `next` controls. `set_model` affects the next provider request,
  `enqueue_user_message` appends a user message before the next LLM call, and
  applied commands are marked in the queue.
- Added runtime event names for control/queue activity and emit
  `command_dequeued` plus `control_applied` when step-boundary controls are
  drained.
- SDK queue operations now emit `control_requested`, `command_queued`, and
  `command_cancelled` events when the control is scoped to a `run_id`.
- Chat-demo backend exposes typed steering control and queued-command
  cancellation endpoints backed by the shared command queue store.
- Chat-demo frontend supports enqueue-user-message steering from the streaming
  composer and next-boundary model switching from the model picker, shows
  cancellable queued steering chips, and updates chip state from control
  lifecycle stream events.
- Chat-demo replay now includes a compact steering timeline for
  control/queue events.
- Chat-demo session history now persists steering controls in
  `metadata_by_run[run_id].steering_controls`; cancelling a queued command
  updates the persisted status, and the frontend restores these controls when
  loading a session.
- Current Playwright mid-run steering check waits for a live `run_id`, queues
  an `enqueue_user_message` control through the composer, verifies the visible
  chip, and writes `/tmp/agent-driver-chat-demo-mid-run-steering.png`.
- Added optional Instructor spike boundary: `agent-driver[instructor]` keeps
  Instructor out of default installs, `agent_driver/structured/` exposes
  structured validation failures as observation-friendly payloads, a prototype
  steering parser returns typed `ControlRequest`, and a plan draft validator
  checks approval-plan structure before artifact creation.
- Started native subagent spawn: successful `agent_tool` envelopes are now
  converted into runtime `planned_subagent_group` metadata, sync child
  execution persists the group/run rows with idempotency keys, and child-level
  subagent events are emitted through the runtime callback path.
- Tightened subagent idempotent persistence so a pending child row is replaced
  by its terminal update instead of staying `running` under the same
  idempotency key.
- Closed Phase 5 native subagent controls: `task_stop_tool` cancels child rows
  and `send_message_tool` records continuation messages for existing children.
- Started Phase 6 mailbox foundation: added durable parent/subagent mailbox
  contracts and in-memory/SQLite stores, and mirrored continuation messages
  into mailbox items for future background workers.
- Closed Phase 6 background lane: `asyncio_background` schedules child runs
  without blocking the parent, status/collection APIs expose durable progress,
  completion notifications flow through mailbox and `later` commands, parent
  aborts cascade into children, and scheduling backpressure enforces declared
  group limits.

Final closure checklist, 2026-05-29:

- [x] Native subagent spawn is covered by the force-planning gate.
- [x] Policy-denied tool cards remain visible in live transcript and replay;
  terminal tool outcomes now survive assistant tombstones.
- [x] `planning_hint` covers request text and planned tool batches:
  side-effecting tools, native `agent_tool`, and expected step count.
- [x] Chat-demo force-planning default is documented as
  `required_for_writes` when force planning is enabled.
- [x] Chat-demo concept smoke covers planning approval, clarification,
  denied tool feedback, web-search final answers, and subagent final answers.
- [x] Live frontend check against configured OpenRouter/web tools produced a
  final answer and screenshot: `/tmp/chat-demo-live-web-check.png`.

## Periodic Product Checks

Backend-only completion is not enough for this workstream. Every phase must
include a chat-demo integration checkpoint:

- expose the new runtime concept through `examples/chat-demo/backend` when it
  affects users;
- render or operate the concept in `examples/chat-demo/frontend`;
- run targeted backend/frontend tests;
- start the demo locally and verify the main user path with Playwright;
- run the concept smoke suite when the phase touches planning, HITL,
  steerability, subagents, replay, or tool policy:
  `make test-chat-concepts CHAT_DEMO_URL=http://localhost:5174`;
- capture at least one screenshot or DOM assertion for the changed surface;
- document any deferred UI gap in this file before moving to the next phase.

Phase-end Playwright concept scenarios should stay short and human-shaped:
each scenario should cover roughly 5-7 visible chat actions or checks, using
phrases that a real reviewer would type. Deterministic SSE mocks are preferred
for regression gates, while live-provider checks remain optional exploratory
checks.

Current demo-gate status:

- Python Playwright installed in the repo `.venv`; Chromium browser installed.
- Phoenix tracing is integrated into the chat-demo Docker dev stack. The
  `phoenix` service exposes UI at `http://localhost:6006`; backend spans are
  exported to project `agent-driver-chat-demo` through
  `PHOENIX_COLLECTOR_ENDPOINT`.
- Phoenix trace analysis, 2026-05-30:
  - initial integration showed exporter errors because OTLP HTTP posted to
    `/`; fixed by normalizing the endpoint to `/v1/traces`;
  - replayed the Fender report scenario through the live backend/front-end and
    inspected project traces in Phoenix (`3` traces, `229` spans in the first
    captured batch);
  - trace pattern confirmed the product issue: after a clarification turn the
    next "напиши реферат, не план" request still triggered a fresh `todo_write`
    and more research tools instead of moving to the deliverable;
  - earlier screenshots and replay also showed public web chat exposing modal
    `enter_plan_mode` / `exit_plan_mode_v2`, which can turn a research/report
    task into approval-plan churn.
- Phoenix-driven corrective plan:
  - [x] Split planning tool packs into live progress tools and modal approval
    tools.
  - [x] Keep public chat presets on live progress planning only
    (`todo_write`, `planning_state_update`, `ask_user_question`), while dev/all
    surfaces can still exercise approval planning.
  - [x] Tighten chat policy: approval plan mode is for implementation/risky
    side effects, not research/writing deliverables; "напиши/черновик/не план"
    must proceed to the requested answer.
  - [x] When a session checklist already exists and the user asks for a
    deliverable, runtime prompt guidance must prioritize final answer delivery
    over restarting the checklist.
  - [x] Re-run the Fender report scenario after the first fix: Phoenix/live SSE
    showed no modal approval tools on the public web preset, but the final turn
    still paused on `ask_user_question` instead of delivering the report.
  - [x] Add a deliverable-request policy guard: for explicit "write/draft/not a
    plan" turns, deny `ask_user_question` and modal plan tools for that run.
  - [x] Add a runtime final-answer guard: once a deliverable-request run has
    used at least one substantive data tool, force the next LLM step to answer
    with `tool_choice=none` instead of continuing research/planning.
  - [x] Re-run the Fender report scenario after the final-answer guard and
    verify the final turn reaches a draft instead of another progress update.

## OpenClaude / Hermes Agent Research Addendum, 2026-05-30

Local sources reviewed:

- `/home/roman/pyprojects/ML/openclaude/src/tools/EnterPlanModeTool/prompt.ts`
- `/home/roman/pyprojects/ML/openclaude/src/tools/EnterPlanModeTool/EnterPlanModeTool.ts`
- `/home/roman/pyprojects/ML/openclaude/src/tools/ExitPlanModeTool/prompt.ts`
- `/home/roman/pyprojects/ML/openclaude/src/tools/ExitPlanModeTool/ExitPlanModeV2Tool.ts`
- `/home/roman/pyprojects/ML/openclaude/src/tools/AskUserQuestionTool/prompt.ts`
- `/home/roman/pyprojects/ML/openclaude/src/tools/TodoWriteTool/prompt.ts`
- `/home/roman/pyprojects/ML/openclaude/src/utils/messages.ts`
- `/home/roman/pyprojects/ML/openclaude/src/utils/attachments.ts`
- `/home/roman/pyprojects/ML/hermes-agent/hermes_cli/kanban_specify.py`
- `/home/roman/pyprojects/ML/hermes-agent/hermes_cli/kanban_decompose.py`
- `/home/roman/pyprojects/ML/hermes-agent/hermes_cli/kanban_swarm.py`
- `/home/roman/pyprojects/ML/hermes-agent/hermes_cli/default_soul.py`
- `/home/roman/pyprojects/ML/hermes-agent/hermes_cli/profiles.py`
- `/home/roman/pyprojects/ML/hermes-agent/hermes_cli/profile_describer.py`
- `/home/roman/pyprojects/ML/hermes-agent/agent/prompt_builder.py`
- `/home/roman/pyprojects/ML/hermes-agent/agent/context_compressor.py`
- `/home/roman/pyprojects/ML/hermes-agent/agent/onboarding.py`
- `/home/roman/pyprojects/ML/hermes-agent/agent/shell_hooks.py`
- `/home/roman/pyprojects/ML/hermes-agent/hermes_cli/plugins.py`
- `/home/roman/pyprojects/ML/hermes-agent/gateway/run.py`
- `/home/roman/pyprojects/ML/hermes-agent/gateway/platforms/base.py`
- `/home/roman/pyprojects/ML/hermes-agent/toolsets.py`

Findings from OpenClaude:

- Plan mode is a permission mode, not just a tool. `EnterPlanMode` switches the
  session into a read-only planning state; `ExitPlanMode` validates that the
  session is actually in plan mode before asking approval.
- Approval plans are for implementation. The `ExitPlanMode` prompt explicitly
  says not to use it for research/codebase understanding; this is the exact
  boundary our public chat needed.
- Plan mode instructions are injected as attachments, with full and sparse
  reminders. This prevents the model from forgetting mode semantics after long
  tool loops or compaction.
- OpenClaude has an interview-style planning workflow: explore, update the plan
  file, ask user questions only for decisions the code cannot answer, then exit
  plan mode for approval.
- `AskUserQuestion` is structured and bounded: 1-4 questions, 2-4 options, short
  headers, optional previews, unique question/option labels, and an automatic
  "Other" path.
- `AskUserQuestion` is explicitly not plan approval. The prompt forbids asking
  "is this plan ok?" through questions because the user cannot see the plan
  before `ExitPlanMode`.
- `TodoWrite` is progress telemetry, not approval. It is recommended for complex
  multi-step work and requires exactly one in-progress task.
- Plan artifacts are file-backed and durable, with session slug recovery and
  plan-mode exit attachments that tell the model it can now implement.

Findings from Hermes Agent:

- Hermes keeps the base persona small (`default_soul.py`) and moves behavior
  into focused runtime blocks: tool-use enforcement, kanban worker guidance,
  mode hints, and platform/session context. This matches our Python-Zen rule:
  prefer model + prompt contracts before adding heavy orchestration.
- `kanban_specify.py` turns a vague user task into a concrete task spec with
  `Goal`, `Approach`, `Acceptance criteria`, and `Out of scope`. It is one LLM
  pass, lenient about JSON fences, returns expected failures as outcomes, and
  falls back to the original title/body instead of breaking the workflow.
- `kanban_decompose.py` creates a small dependency graph only when useful:
  usually 2-6 self-contained child tasks, `parents` as same-list dependency
  indexes, profile routing by role description, and fallback assignees so no
  child task is stranded.
- `kanban_swarm.py` builds worker -> verifier -> synthesizer topology on top
  of the existing kanban primitives. The root task is a shared blackboard/audit
  anchor, workers write structured handoffs, the verifier gates with explicit
  pass/block metadata, and the synthesizer starts only after the gate passes.
- Kanban worker prompts are strict about lifecycle: heartbeat on long work,
  block on genuine ambiguity, complete only with structured summary/metadata,
  and create follow-up tasks instead of scope-creeping. Headless workers must
  block rather than call `clarify`.
- Gateway steerability is explicit and non-destructive: `/steer <prompt>` or
  `busy_input_mode=steer` injects user direction after the next tool call; if
  the agent is not ready or cannot steer, Hermes falls back to queue semantics.
- Busy input has clear modes (`interrupt`, `queue`, `steer`) and one-time
  onboarding hints, so users understand whether their message interrupted a
  run, queued a follow-up, or steered the current run.
- Hooks/plugins provide thin policy extension points: `pre_tool_call` can veto
  with a block message, `pre_llm_call` can inject context, and result/output
  transforms are centralized. This is useful for policy and observability
  without embedding every rule into the core loop.
- Context compression is protective rather than eager: prune old tool results,
  preserve head and recent tail, clean orphan tool-call/result pairs, support
  focused compression, and back off after ineffective compression attempts.
- Profiles isolate config, env, memory, sessions, skills, logs, plans,
  workspace, and home directories. For `agent-driver` this is a later-stage
  subagent isolation option, not something to pull into the first pass.

Best-of-both plan for `agent-driver`:

- [x] Separate live progress planning from modal approval planning at the tool
  pack level.
- [x] Keep public chat on progress-only planning; reserve approval planning for
  implementation/dev/risky side-effect contexts.
- [x] Treat explicit deliverable turns as a run-level contract:
  `produce_deliverable`, with clarification and approval tools denied unless a
  runtime safety gate requires them.
- [x] Add a first version of the deliverable final-answer gate after substantive
  data tools, matching the Hermes verifier/synthesizer idea that work must
  materialize into a concrete output before the run is allowed to wander again.
- [x] Add OpenClaude-style mode attachments:
  `planning_mode_active`, `planning_mode_sparse`, `planning_mode_exit`, and
  `deliverable_request_active`, injected as structured runtime reminders rather
  than one-off prompt strings.
- [x] Replace single-string `ask_user_question` with a structured question
  contract: 1-4 questions, headers, 2-4 options, optional "Other", optional
  preview, uniqueness validation, and a response payload that maps question to
  answer.
  Current implementation keeps old `prompt`/`choices` compatibility, adds the
  structured `questions` payload, validates bounds/uniqueness, and renders
  option buttons plus the freeform "Other" path in chat-demo while resuming
  through the existing clarification message path.
- [x] Add an `AskUserQuestion` policy classifier: allow only for
  user-owned decisions or truly blocking missing information; deny when the
  current turn asks for a final deliverable and enough context exists.
  Current chat-demo policy marks deliverable turns with `deliverable_request`,
  hides `ask_user_question` from the model schema via `denied_tools`, and keeps
  a runtime denial fallback if a provider still attempts the tool.
- [x] Add a Hermes-style "specifier" preflight for vague complex tasks:
  produce a compact `Goal / Approach / Acceptance criteria / Out of scope`
  contract before planning/decomposition. Keep it one-shot, optional, and
  lenient on parse failure.
  Current implementation is a deterministic lightweight chat task contract:
  `deliverable`, `research`, and `implementation` contracts are attached to
  chat-demo policy metadata and rendered as compact runtime reminders. This
  keeps the first pass in the "model + prompt + small guard" lane rather than
  adding a new LLM preflight or workflow graph.
- [ ] Add a Hermes-style execution blueprint for long research/writing and
  implementation chat tasks: small typed phases, self-contained worker specs,
  required handoff outputs per phase, verifier gate, synthesizer final answer,
  and explicit block/retry/error policy. Use this only when Phoenix traces show
  the plain prompt+tool loop keeps re-planning or losing the deliverable.
  Current small guard: deliverable requests now force `tool_choice=none` after
  either substantive data tools or progress-only planning tools
  (`todo_write`/`planning_state_update`), preventing "plan again instead of
  answer" loops while still avoiding a full DAG.
- [ ] Extend steerability semantics with a Hermes-like boundary: user messages
  during a running chat turn should be represented as `interrupt`, `queue`, or
  `steer after next tool result`, with deterministic UI affordances and trace
  labels for each path.
- [ ] Add policy hook points inspired by Hermes plugins: a narrow
  `pre_tool_call` veto/context hook and a `post_tool_result` transform hook,
  implemented as optional runtime callbacks before considering a larger plugin
  system.
- [ ] Add a compression/compaction note for later phases: preserve active task
  contracts, recent tail, and tool-call/result integrity; back off if
  compression is ineffective rather than repeatedly rewriting context.
- [ ] Add Phoenix-backed regression scenarios for:
  plan -> clarification -> answer, research -> deliverable, implementation
  plan approval, subagent final synthesis, and ask-question denial on
  deliverable turns.
  Current concept-smoke suite covers deterministic UI regressions for
  clarification, plan approval, denied tools, web-search final answer,
  subagent synthesis, simple direct answers without planning, denied
  clarification on deliverable turns, deliverable-without-replanning, and
  plan -> web execution -> final answer. Live Phoenix-backed passes should
  reuse the same scenario labels where possible and compare trace shape against
  the deterministic expectation.
- Added `examples/chat-demo/frontend/tests/e2e/chat_concepts_smoke.py` for
  deterministic concept checks over the real React UI. Current scenarios cover
  plan approval, plan/tombstone/clarification/resume, denied tool feedback,
  web-search final answer, and subagent final answer.
- Current concept smoke writes screenshots to `/tmp/chat-demo-concepts` and
  can be run through `make test-chat-concepts`.
- Scenario methodology: run a small orthogonal set first, inspect Phoenix
  traces for model/tool order, then apply the smallest prompt/contract/runtime
  change that fixes the trace. The baseline set is:
  `simple-direct`, `web-search-final`, `clarification`,
  `ask-question-denied`, `deliverable-no-replan`, `plan-web-answer`,
  `plan-approval`, and `subagent-final`.
- Concept smoke check, 2026-05-30: full deterministic Playwright suite passed
  against `http://localhost:5174` with scenarios `ask-question-denied`,
  `clarification`, `deliverable-no-replan`, `denied-tool`, `plan-approval`,
  `plan-web-answer`, `simple-direct`, `subagent-final`, and
  `web-search-final`.
- Live Phoenix check, 2026-05-30:
  - `simple-direct` prompt (`сколько r в слове strawberry?`) completed with no
    tool calls, matching the no-planning expectation;
  - `web-search-final` prompt produced `web_search -> web_fetch -> final`,
    with trace `run_0455d4ec910b` visible in Phoenix under
    `agent-driver-chat-demo`;
  - trace inspection showed `chat.session_id` was empty on root spans, so
    chat-demo now includes `session_id` in run `app_metadata` for cleaner
    Phoenix filtering.
- Live Phoenix check, 2026-05-30, later pass:
  - `research-report` initially failed because Qwen/OpenRouter streamed forced
    tool arguments as text (`0.5, "query": ... </tool_call>`) and the run
    looped through text-form tool calls;
  - fixes added provider fallback for split/forced text-form tool calls,
    research `requires_research=True`, and force-final after research evidence;
  - `research-report` now passes against `localhost:5174` with
    `run_16b2796ea7f0`, `tool_names=['web_search', 'web_search']`, terminal
    `run_completed`, and no trace-summary failure flags;
  - `simple-direct` rechecked after the provider/runtime fixes with
    `run_82c3ff4d12fc`, no tools, terminal `run_completed`.
- Root `.venv` has backend/frontend test dependencies installed for local
  checks; the stale `examples/chat-demo/backend/.venv` is no longer used.
- Frontend unit tests pass.
- Backend plan approval scenario passes in-process.
- Dev compose is running at `http://127.0.0.1:5174` with backend
  `http://127.0.0.1:8010`, hot reload enabled, and provider settings loaded
  from repo `.env`.
- Playwright smoke against the dev compose verifies the real configured
  provider (`openrouter`) and writes
  `/tmp/agent-driver-chat-demo-openrouter.png`.
- Earlier Playwright smoke covered the Force planning toggle; the current
  public web UX keeps planning always-on and no longer exposes that toggle as a
  user-facing control.
- Playwright smoke verifies replay rendering for a force-planning blocked write
  and writes `/tmp/agent-driver-chat-demo-force-planning-block.png`.
- Current Playwright replay DOM check verifies the post-design policy-denied
  card for `file_write` and writes
  `/tmp/agent-driver-chat-demo-force-planning-block-current.png`.
- Playwright DOM check verifies the replay steering timeline after queueing a
  chat-demo control command and writes
  `/tmp/agent-driver-chat-demo-steering-replay.png`.
- Optional deterministic plan approval browser smoke can be run by restarting
  dev compose with `AGENT_DRIVER_PROVIDER=fake` and
  `CHAT_DEMO_FAKE_SCENARIO=plan_approval`.
- Optional deterministic force-planning denial smoke can be run by restarting
  dev compose with `AGENT_DRIVER_PROVIDER=fake`,
  `CHAT_DEMO_FAKE_SCENARIO=force_planning_block`, and
  `CHAT_DEMO_FORCE_PLANNING=true`.

Phase-specific chat-demo gates:

- Phase 1: plan approval card can show plan content/hash/path and approve,
  edit, reject, or cancel through existing resume endpoints.
- Phase 2: forced planning policy visibly blocks risky execution in replay and
  gives the next model turn structured remediation toward plan approval.
- Phase 3-4: mid-run steering controls appear in chat-demo and survive SSE
  reconnect/replay. Current checkpoint: composer enqueue/cancel controls are
  visible while streaming, and replay shows persisted control lifecycle events.
- Phase 5-6: subagent spawn, background status, mailbox notifications,
  continue and stop are covered by runtime tests and by the chat-demo
  `agent_tool` concept smoke.
- Phase 7: coordinator/worker behavior is covered by runtime/eval tests and
  the chat-demo subagent synthesis smoke.

## Execution Todo Backlog

This checklist is the live execution board for the roadmap. Keep it updated
when a slice is implemented, tested, committed, or intentionally deferred.

### Phase 1: Planning Artifact And Approval Gate

- [x] Add `PlanArtifact`, `PlanningModeState`, and `PlanApprovalPayload`
  contracts.
- [x] Add in-memory plan artifact lifecycle helpers.
- [x] Wire `exit_plan_mode_v2` plan content to
  `plan_approval_required` interrupts.
- [x] Support approve/edit resume metadata for approved plans.
- [x] Show plan approval cards in chat-demo.
- [x] Add deterministic plan-approval fake scenario and backend tests.
- [x] Add SQLite or durable plan artifact persistence beyond process-local
  helpers.
- [x] Emit dedicated plan lifecycle runtime events:
  `plan_mode_entered`, `plan_artifact_updated`, `plan_approval_requested`,
  `plan_approved`, `plan_rejected`.
- [x] Add checkpoint/resume tests for awaiting plan approval after process
  restart or durable store reload.

### Phase 2: Force Planning Policy Engine

- [x] Add runtime gate for write/external side-effect tools.
- [x] Keep planning tools exempt from force-planning gate.
- [x] Add model-facing remediation for force-planning denials.
- [x] Add adaptive chat prompt guidance for voluntary plan mode.
- [x] Add deterministic `planning_hint` classifier with English/Russian tests.
- [x] Attach `planning_hint` metadata in chat-demo.
- [x] Add configurable evaluator modes:
  `off`, `prompt_only`, `required_for_writes`,
  `required_for_risky_tools`, `always_for_multistep`.
- [x] Wire chat-demo env config for force-planning mode.
- [x] Add typed `PlanningPolicyInput` contract/normalizer for metadata instead
  of relying on ad hoc dictionaries.
- [x] Extend `planning_hint` to planned tool batches:
  side-effecting tools, native `agent_tool`, expected step count.
- [x] Gate native subagent spawn once `agent_tool` becomes a runtime spawn
  surface.
  Current `agent_tool` request envelope is `external_action` and now has an
  explicit force-planning regression test; native spawn should preserve that
  manifest/policy boundary.
- [x] Run and document a passing current Playwright smoke for chat-demo
  force-planning policy-denied replay after the latest design changes.
  2026-05-29 attempt: backend replay passed, live UI card rendering failed and
  was moved to the chat-demo design backlog.
  2026-05-29 current check: replay DOM asserts `file_write`, `denied`, and the
  force-planning remediation text; screenshot:
  `/tmp/agent-driver-chat-demo-force-planning-block-current.png`.
- [x] Decide and document chat-demo default mode:
  `prompt_only` or `required_for_writes`.
  Current summary lives in [Planning and control](planning-and-control.md):
  keep `required_for_writes` when force planning is enabled.

### Phase 3: Steering Contracts And Queue

- [x] Add `agent_driver/contracts/control.py` with `ControlRequest`,
  `ControlResponse`, and `CommandQueueItem`.
- [x] Add command queue stores:
  in-memory first, SQLite second.
- [x] Add control dispatcher/store priority semantics:
  `now > next > later`, FIFO within priority.
- [x] Add SDK methods:
  `control`, `enqueue`, `set_model`, `set_permission_mode`,
  `cancel_queued_message`.
- [x] Drain queue at runtime step boundaries.
- [x] Emit typed control/queue runtime events:
  `control_requested`, `command_queued`, `command_dequeued`,
  `command_cancelled`, and `control_applied`.
- [x] Add tests for priority, FIFO, cancellation, checkpoint/restart, and
  `set_model` affecting the next LLM request.
  Priority/FIFO/cancellation/dedupe route tests are done; SQLite queue
  persistence covers store restart, and SDK runtime tests cover pre-LLM
  checkpoint restart plus `set_model`/queued-message request effects.

### Phase 4: User Steering UX Adapters

- [x] Extend SSE projection for control/queue events.
- [x] Add chat-demo/backend control endpoints.
- [x] Add chat-demo/frontend controls for enqueue/cancel/interrupt/model
  switch where product-appropriate.
  Enqueue-user-message steering is wired into the streaming composer with
  queued-command cancellation; selecting a model while streaming queues a
  next-boundary `set_model` command.
- [x] Persist steering operations in session transcript/history.
  Chat-demo writes queue lifecycle snapshots to
  `metadata_by_run[run_id].steering_controls` and restores them in the
  frontend store when a session is loaded.
- [x] Add replay view support for queued messages and controls.
- [x] Verify mid-run steering with Playwright and record screenshot/DOM
  assertion.
  Current DOM check waits for `run 1`, posts through the composer, verifies the
  queued steering chip, and writes
  `/tmp/agent-driver-chat-demo-mid-run-steering.png`.

### Phase 4a: Optional Instructor Spike

- [x] Add optional dependency extra without affecting default installs.
- [x] Add `agent_driver/structured/` adapter boundary.
- [x] Prototype one steering parser into typed `ControlRequest`.
- [x] Prototype one plan artifact validator.
- [x] Surface validation/retry failures as structured runtime observations or
  errors.
  `StructuredExtractionFailure.as_observation()` returns a serializable
  payload that runtime adapters can publish as observations/warnings.

### Phase 5: Native Agent Tool Spawn

- [x] Make `agent_tool` a runtime-recognized spawn request.
- [x] Convert tool envelopes into `SubagentGroupSpec`.
  Runtime now maps `agent_tool` `subagent_request` envelopes into
  `planned_subagent_group`, then reuses the existing `SubagentGroupSpec`
  conversion path.
- [x] Persist group before child execution with idempotency keys.
  The sync executor already persists group/run rows before child execution;
  `agent_tool` request ids flow into task id/idempotency fields.
  Store tests now assert pending idempotent rows update to terminal status.
- [x] Pass subagent event callback through sync execution.
  Child `subagent_started` / `subagent_completed` callbacks are projected into
  parent runtime events.
- [x] Add native `task_stop_tool`.
  `task_stop_tool` now accepts native subagent ids and the runtime marks the
  matching child row as `cancelled`, emitting subagent/control lifecycle events.
- [x] Add `send_message_tool` continuation semantics for existing child
  context.
  Parent-to-child messages now append bounded continuation entries to the
  existing child row; Phase 6 can move this metadata mailbox into durable
  background delivery.
- [x] Add tests for spawn, resume idempotency, continuation, stop, and events.

### Phase 6: Background Subagents And Mailbox

- [x] Add `asyncio_background` subagent backend.
  Planned groups can now set `execution_mode: asyncio_background`; the runtime
  schedules child runs with `asyncio.create_task`, returns parent control
  immediately, and emits completion notifications through mailbox/`later`
  commands when children finish.
- [x] Add durable mailbox for messages, permissions, plan approvals, and task
  notifications.
- [x] Queue child-to-parent notifications as `later` commands.
  Child completion events now enqueue deferred parent notifications through the
  steering command queue and mirror them into the subagent mailbox.
- [x] Add status polling and collection APIs.
  `agent_driver.subagents` now exposes a bounded status snapshot and mailbox
  collection helper for parent runs.
- [x] Propagate parent abort to children.
  Sync subagent execution now derives child abort handles from the parent and
  persists cancelled child rows when the parent is already aborted.
- [x] Add budgets/backpressure for child/group scheduling.
  Sync group scheduling now applies `max_parallel`, `token_budget`, and
  `cost_budget_usd` before starting child runs and records skipped tasks.

### Phase 7: Coordinator Profile

- [x] Add coordinator profile/config.
  `AgentProfile.COORDINATOR` is now a first-class run profile.
- [x] Add coordinator prompt snapshot based on OpenClaude principles.
  The prompt pins self-contained worker tasks, existing-worker continuation,
  no fake worker results, provenance-aware synthesis, and verifier usage.
- [x] Add worker definitions: `worker`, `researcher`, `implementer`,
  `verifier`.
- [x] Restrict coordinator/worker tool surfaces.
  Worker tasks now narrow child `ToolPolicyInput.allowed_tools` by role while
  preserving parent deny lists and metadata.
- [x] Add scratchpad/artifact handoff rules.
  Child handoff metadata now carries role rules, bounded scratchpad policy, and
  refs-only artifact handoff requirements through completed child rows.
- [x] Add evals for research fan-out, corrected continuation, and verifier
  catch.
  Offline eval-style tests now pin role-restricted research fan-out, corrected
  parent-to-child continuation, and verifier critique preservation.

### Phase 8: Isolation And Advanced Backends

- [x] Add worktree isolation for child runs.
  Child tasks can request `metadata.isolation_mode="worktree"`; the runtime
  creates a detached git worktree, runs the child there, and removes it after
  completion/cancellation.
- [x] Add cwd override with policy validation.
  Child runs inherit parent `workspace_cwd`; per-task `cwd`/`workspace_cwd`
  overrides are accepted only when they resolve inside the parent workspace.
- [x] Evaluate process backend after `asyncio_background`.
  Decision: keep process/tmux/remote launch as future adapters. Current
  `asyncio_background` plus mailbox, abort propagation, cwd/worktree isolation,
  and bounded artifact refs covers the planned local runtime path without
  adding cross-process serialization and lifecycle risk.
- [x] Add bounded artifact refs for child outputs.
  Completed child rows now keep a bounded `child_artifact_refs` list, audit
  dropped refs, expose the first artifact as `output_pointer`, and mark
  artifact refs in merge provenance.
- [x] Add cleanup tests for completed/cancelled children.
  Background tests now pin group finalization after completed and cancelled
  child rows reach terminal state.

### Documentation And Recipes

- [x] Update `docs/roadmap.md` with a pointer to this plan.
- [x] Fold force-planning, steering, and subagent summaries into the current
  short docs: [Planning and control](planning-and-control.md),
  [Runtime overview](runtime.md), and [Chat demo](chat-demo.md).
- [x] Add SDK recipes for plan approval, mid-run steering, child continuation,
  and stopping a child.

### End-Of-Phase Quality Pass

At the end of every phase, reserve a separate implementation item for real
refactoring and code-quality improvement:

- Run focused `pylint` over the touched runtime/domain packages.
- Prefer fixing design issues, naming, decomposition, typing, imports, and
  duplicated logic over suppressing warnings.
- Add `disable` pragmas only when the warning is genuinely inappropriate for
  the local design, and document why in code or in the phase notes.
- Keep the quality pass scoped to the phase's touched modules unless a broader
  cleanup is explicitly planned.

## Optional Structured Extraction: Instructor Spike

Reference: `https://python.useinstructor.com/`.

Instructor is not a replacement for `agent-driver` runtime, providers, event
log, checkpoints, HITL, or governed tools. Its best fit is an optional,
schema-first extraction layer for places where the runtime needs "LLM output
as a validated Pydantic object" with retry/reask semantics.

Recommended scope:

- Add optional dependency extra: `agent-driver[instructor]`.
- Add an adapter under `agent_driver/structured/`, for example
  `extract_structured(messages, response_model, purpose=...)`.
- Keep provider/runtime contracts independent of Instructor; the adapter should
  consume existing `LlmRequest`/provider configuration or wrap an external
  Instructor client at the edge.

High-value use cases in this roadmap:

- Phase 3 steerability: parse natural-language steering such as "stop the
  worker", "switch to cheaper model", "continue but ask before writes" into a
  typed `ControlRequest`.
- Phase 1-2 force planning: validate plan artifacts against a schema containing
  scope, steps, touched resources, risks, verification, rollback, and requested
  permission categories before approval.
- Phase 5-7 subagents: validate `SubagentTaskSpec`, worker reports, research
  findings, coordinator synthesis, and plan-approval mailbox messages.
- Memory/compaction: extract durable facts, decisions, unresolved questions,
  and user preferences from transcripts into typed context records.

Acceptance criteria for the spike:

- Instructor remains optional and disabled by default.
- Existing provider tests pass without Instructor installed.
- A focused prototype demonstrates one steering parser and one plan artifact
  validator using existing Pydantic contracts.
- Validation/retry failures are surfaced as runtime observations or structured
  errors, not hidden inside provider-specific exceptions.

## Source Analysis

### OpenClaude: что стоит перенять

#### Force planning

Релевантные источники:

- `src/tools/ExitPlanModeTool/ExitPlanModeV2Tool.ts`
- `src/tools/ExitPlanModeTool/prompt.ts`
- `src/commands/plan/plan.tsx`
- `src/utils/plans.ts`
- `src/bootstrap/state.ts`

Сильные идеи:

- Plan mode является явным permission mode, а не просто подсказкой в system
  prompt.
- `ExitPlanModeV2` не принимает план текстом от модели. Модель пишет план в
  plan file, tool читает файл и показывает пользователю именно сохраненный
  артефакт.
- Выход из plan mode является approval interrupt: пользователь может
  подтвердить, отклонить или изменить план.
- У plan artifact есть стабильный path/slug, восстановление при resume/fork и
  отдельные файлы для subagents.
- OpenClaude отличает research-only задачи от implementation planning:
  `ExitPlanMode` не надо использовать для чистого анализа.
- После approval модель получает обратно approved plan и hint обновить todo.
- Для teammates есть leader approval через mailbox:
  `plan_approval_request` / `plan_approval_response`.
- Есть связка с allowed prompts: план может запросить категории разрешений
  вроде "run tests", чтобы не просить approval на каждый похожий tool call.

#### Steerability

Релевантные источники:

- `src/entrypoints/sdk/controlSchemas.ts`
- `src/utils/messageQueueManager.ts`
- `src/context/QueuedMessageContext.tsx`
- `src/QueryEngine.ts`
- `src/bootstrap/state.ts`
- `src/bridge/bridgeMessaging.ts`

Сильные идеи:

- Control protocol отделен от обычных user messages:
  `interrupt`, `set_model`, `set_permission_mode`,
  `set_max_thinking_tokens`, `cancel_async_message`, `stop_task`,
  MCP controls, settings updates.
- Есть приоритетная command queue:
  `now > next > later`, FIFO внутри приоритета.
- Очередь принимает обычный пользовательский input, async notifications,
  channel messages, task notifications и queued commands.
- Можно удалять pending async message по uuid.
- Queue operations пишутся в transcript, что важно для resume/replay.
- Mid-run steering не смешивается бездумно с prompt history: часть команд
  исполняется как control request, часть как user-role continuation, часть как
  system/task notification.
- Модель/permission mode/thinking budget можно менять во время сессии без
  полного перезапуска.

#### Subagents

Релевантные источники:

- `src/tools/AgentTool/AgentTool.tsx`
- `src/tools/shared/spawnMultiAgent.ts`
- `src/coordinator/coordinatorMode.ts`
- `src/coordinator/workerAgent.ts`
- `src/utils/teammateMailbox.ts`
- `src/tasks/LocalAgentTask/LocalAgentTask.ts`
- `src/tasks/InProcessTeammateTask/*`
- `src/tools/SendMessageTool/*`
- `src/tools/TaskStopTool/*`

Сильные идеи:

- `AgentTool` умеет не только синхронный child result, но и background agents.
- Worker получает self-contained prompt. Coordinator prompt явно запрещает
  "based on your findings" и требует синтезировать конкретный follow-up.
- Есть адресуемость workers: `name`, `team_name`, `agent_id`.
- Есть `SendMessage` для продолжения уже запущенного worker с его контекстом.
- Есть `TaskStop` для остановки ошибочно запущенного worker.
- Есть mailbox для teammate-to-leader и leader-to-teammate сообщений,
  permission requests и plan approval.
- Есть isolation modes: отдельный worktree, cwd override, remote launch.
- Coordinator mode имеет отдельный system prompt, tool set и workflow:
  research fan-out, synthesis by coordinator, implementation, verification.
- Есть in-process teammate path и pane/tmux backend path; это хороший намек на
  backend-neutral execution interface.

## Current Agent-Driver State

### Уже реализовано или частично реализовано

- `todo_write`, `planning_state_update`, `enter_plan_mode`,
  `exit_plan_mode_v2`, `ask_user_question` в `agent_driver/tools/planning.py`.
- Planning state хранится в run metadata и проецируется в plan snapshot:
  `agent_driver/runtime/single_agent/step_planning.py`.
- Prompt policy уже требует `todo_write` для планов:
  `agent_driver/prompts/templates/react_chat_tool_policy.txt`.
- Есть todo reminders и progress hints:
  `agent_driver/runtime/single_agent/todo_reminders.py`.
- Есть `InterruptRequest`, `ResumeCommand`, allowed prompt patterns:
  `agent_driver/contracts/interrupts.py`.
- SDK facade умеет `run`, `resume`, `approve`, `reject`, `edit`,
  `cancel`, `clarify`:
  `agent_driver/sdk/agent.py`.
- SSE projection поверх durable runtime events:
  `agent_driver/adapters/sse.py`,
  `agent_driver/runtime/stream/projection.py`.
- Есть subagent contracts, stores, sync execution, handoff, join/merge:
  `agent_driver/subagents/*`.
- Есть `agent_tool` request envelope и session-local messaging/team tools:
  `agent_driver/tools/builtin/agent.py`,
  `agent_driver/tools/builtin/messaging.py`.

### Главные разрывы

- `enter_plan_mode` / `exit_plan_mode_v2` сейчас только меняют metadata, но не
  создают полноценный persisted plan artifact и не инициируют approval flow.
- Force planning не является runtime gate. Сейчас это в основном prompt policy.
- Нет отдельного control-plane контракта для steering. Управление размазано
  между `AgentRunInput`, metadata, abort/resume и host-specific логикой.
- Нет durable command queue с приоритетами, uuid, replay и cancellation.
- Subagents запускаются через metadata `planned_subagent_group`; `agent_tool`
  пока request envelope, не прямой spawn trigger.
- Нет background child executor, адресуемых workers, mailbox-backed continue,
  task stop и leader approval для child plan mode.
- Subagent event callback в executor есть, но runtime stage пока не прокидывает
  его внутрь `execute_subagent_group_sync`.

## Target Architecture

### 1. Force Planning Layer

Добавить новый слой поверх существующего planning state:

- `PlanningModeState`: `disabled | collecting | awaiting_approval | approved |
  rejected | expired`.
- `PlanArtifact`: durable markdown artifact с `plan_id`, `run_id`,
  `thread_id`, `agent_id`, `path`, `content_hash`, `created_at`,
  `approved_at`, `approved_by`.
- `PlanApprovalInterrupt`: специализированный interrupt reason/payload для
  plan approval.
- `PlanningGate`: policy hook перед tool stage/subagent spawn/file write/shell,
  который проверяет, нужен ли approved plan.

Ключевой принцип: `todo_write` остается live-progress checklist; plan artifact
является approval документом для начала исполнения.

Adaptive planning principle:

- Planning tools should be available to the model by default, but not forced
  for every prompt. Simple factual answers, typo fixes and narrowly specified
  edits can stay direct.
- The model should proactively enter plan mode for non-trivial implementation:
  new features, multi-file changes, architectural choices, unclear
  requirements, risky behavior changes, or tasks where user preference affects
  the approach.
- Runtime policy should only force plan approval at safety boundaries
  (`required_for_writes`, `required_for_risky_tools`,
  `always_for_multistep`). This keeps the Claude Code-like behavior where
  planning is chosen for complex work without making every interaction modal.

### 2. Steering Control Plane

Добавить transport-neutral control protocol:

- `ControlRequest`:
  `interrupt`, `enqueue_user_message`, `cancel_queued_message`,
  `set_model`, `set_tool_policy`, `set_permission_mode`,
  `set_max_thinking_tokens`, `patch_planning_state`, `stop_subagent`,
  `continue_subagent`, `get_context_usage`.
- `ControlResponse`: success/error + optional pending approvals.
- `CommandQueueItem`: `queue_id`, `run_id`, `thread_id`, `agent_id`,
  `priority`, `kind`, `payload`, `created_at`, `source`, `dedupe_key`,
  `status`.
- `CommandQueueStore`: in-memory + SQLite first, protocol for Postgres later.

Steering semantics:

- `now`: interrupt/stop/cancel/critical user correction.
- `next`: user follow-up for next model boundary.
- `later`: task notifications, background summaries, scheduled messages.
- Mid-run controls apply at deterministic step boundaries unless explicitly
  marked interrupting.
- Every queue mutation emits typed runtime events and can be replayed.

### 3. Subagent Orchestration Layer

Поверх существующих `SubagentGroup`/`SubagentRun` добавить:

- `SubagentRuntime`: backend-neutral interface:
  `spawn`, `continue_run`, `stop`, `list`, `poll`, `collect`.
- Execution backends:
  `sync` first-class; `asyncio_background`; later process/tmux/remote.
- `SubagentMailboxStore`: durable message and approval records.
- `agent_tool` native runtime integration:
  model calls `agent_tool`, runtime turns request into group/task rows and
  schedules execution.
- `send_message_tool` native integration:
  continue existing child by `agent_id`/`name`.
- `task_stop_tool` built-in:
  cancel/stop child and propagate abort handle.
- Coordinator profile/prompt:
  explicit worker workflow, self-contained prompts, no fake results,
  use existing workers when context is valuable.

## Work Plan

### Phase 1: Planning Artifact And Approval Gate

Scope:

- Add contracts for `PlanArtifact`, `PlanningModeState`,
  `PlanApprovalPayload`.
- Add plan artifact store under `agent_driver/context/planning/` with
  in-memory and SQLite implementations or reuse artifact store if cleaner.
- Extend `enter_plan_mode` to create/activate a plan artifact.
- Replace current `exit_plan_mode_v2` behavior with:
  read current plan artifact;
  validate non-empty;
  emit/persist `InterruptRequest(reason=plan_approval_required)`;
  return paused output until approval.
- On approve, mark artifact approved, add approved plan to model-facing context,
  and restore agent mode.
- On edit, update artifact content, hash and approval metadata.
- On reject/cancel, stay in or exit plan mode according to action.
- Add `PlanningGate` before high-risk tools and subagent spawn.

Implementation notes:

- Keep current `todo_write` behavior unchanged.
- Reuse existing `ResumeCommand.approved_prompts` for plan-level allowed
  prompts.
- Add runtime events:
  `plan_mode_entered`, `plan_artifact_updated`,
  `plan_approval_requested`, `plan_approved`, `plan_rejected`.

Tests:

- Contract schema snapshots.
- Tool tests for plan artifact lifecycle.
- Runtime tests for approve/edit/reject/cancel.
- Resume after checkpoint during awaiting approval.
- Gate blocks shell/file write/subagent spawn without approved plan when policy
  requires it.

Exit criteria:

- A code-writing task can be forced into plan mode, paused for approval, then
  resumed without losing plan content or todo state.
- Pure research task can still run without approval when planning policy allows.

### Phase 2: Force Planning Policy Engine

Scope:

- Add `PlanningPolicyInput` to run/tool policy metadata:
  task class, risk threshold, tool categories, files touched, subagent spawn.
- Add configurable planning modes: [done in evaluator; config/UI wiring remains]
  `off`, `prompt_only`, `required_for_writes`, `required_for_risky_tools`,
  `always_for_multistep`.
- Add deterministic classifiers/rules first:
  user asks to implement/change/write/refactor;
  planned tool has side effect;
  model requests `agent_tool`;
  max expected steps > threshold.
- Add adaptive prompt guidance for voluntary planning:
  prefer `enter_plan_mode` for non-trivial implementation, but skip it for
  simple fixes and pure research/exploration.
- Add model-facing remediation when gate blocks:
  "enter plan mode and prepare approval plan".

Tests:

- Rule matrix for common Russian/English task phrasing.
- No false positive for read-only research.
- Planning gate messages are stable and actionable.

Exit criteria:

- Force planning is a runtime policy, not just prompt instruction.

### Phase 3: Steering Contracts And Queue

Scope:

- Add `agent_driver/contracts/control.py`.
- Add `agent_driver/runtime/control/` package:
  queue protocol, in-memory store, SQLite store, dispatcher.
- Add SDK methods:
  `agent.control(...)`, `agent.enqueue(...)`,
  `agent.set_model(...)`, `agent.set_permission_mode(...)`,
  `agent.cancel_queued_message(...)`.
- Add runtime step-boundary drain:
  process `now` controls before next step;
  append `next` user messages before next LLM call;
  keep `later` notifications ordered but non-starving.
- Emit events:
  `control_requested`, `control_applied`, `command_queued`,
  `command_dequeued`, `command_cancelled`.

Tests:

- Priority order `now > next > later`.
- FIFO within priority.
- Cancel queued message by uuid.
- Queue survives checkpoint/resume.
- `set_model` affects next LLM request.
- `interrupt` cancels current/next step deterministically.

Exit criteria:

- A host can steer a live run without mutating opaque run metadata.

### Phase 4: User Steering UX Adapters

Scope:

- Extend SSE stream projection for control/queue events.
- Add CLI/chat-demo API endpoints or SDK examples:
  enqueue message, interrupt, approve/edit plan, set model, stop child.
- Persist steering operations in session transcript/history.
- Add replay view support for queued user messages and control operations.

Tests:

- SSE backfill after reconnect includes queue/control events once.
- Chat-demo integration test for mid-run user correction.
- Replay shows steering timeline.

Exit criteria:

- Steering is visible, replayable and debuggable from adapters.

### Phase 5: Native Agent Tool Spawn

Scope:

- Change `agent_tool` from request envelope only to runtime-recognized spawn
  request.
- Teach tool stage to collect `agent_tool` envelopes and build
  `SubagentGroupSpec`.
- Persist group before child execution; use idempotency keys to avoid duplicate
  spawn on resume.
- Pass subagent event callback into `execute_subagent_group_sync`.
- Add `task_stop_tool` and wire it to abort child runs.
- Add `send_message_tool` continuation semantics for existing child context.

Tests:

- Model-planned `agent_tool` creates group/run rows.
- Parent crash after spawn resumes without duplicate children.
- `send_message_tool` continues an existing child.
- `task_stop_tool` cancels child and emits events.

Exit criteria:

- Subagents are no longer only metadata-driven; the model-facing built-in can
  actually schedule children.

### Phase 6: Background Subagents And Mailbox

Scope:

- Add `asyncio_background` subagent backend.
- Add durable mailbox:
  message, permission request, permission response,
  plan approval request/response, task notification.
- Add child-to-parent notifications as queued `later` command items.
- Add subagent status polling and collection.
- Propagate parent abort to children.
- Add per-child and group budgets/backpressure.

Tests:

- Background child completes after parent turn and queues notification.
- Parent can continue while child runs.
- Parent cancellation stops children.
- Mailbox survives resume.
- Budget exhaustion stops scheduling new children.

Exit criteria:

- Long child tasks can run independently and report back without blocking the
  parent run.

### Phase 7: Coordinator Profile

Scope:

- Add `AgentProfile.COORDINATOR` or profile config.
- Add coordinator system prompt based on OpenClaude principles:
  fan out independent research;
  synthesize findings before implementation;
  self-contained worker prompts;
  do not pretend worker results arrived;
  continue existing workers when useful.
- Add worker agent definitions:
  `worker`, `researcher`, `implementer`, `verifier`.
- Add tool surface restrictions for coordinator/worker.
- Add scratchpad/artifact handoff rules.

Tests:

- Prompt snapshot tests.
- Eval: two research children + coordinator synthesis.
- Eval: failed worker is continued with corrected instructions.
- Eval: verifier catches weak implementation.

Exit criteria:

- Multi-agent behavior is a deliberate profile, not an accidental use of
  generic ReAct prompts.

### Phase 8: Isolation And Advanced Backends

Scope:

- Add worktree isolation for child runs.
- Add cwd override with policy validation.
- Add process backend if needed after `asyncio_background`.
- Keep tmux/remote as optional future adapters, not core runtime dependency.
- Add artifact refs for child outputs rather than full transcript ingestion.

Tests:

- Child writes in worktree do not mutate parent workspace.
- Parent sees bounded child summary + artifact refs.
- Cleanup after completed/cancelled child.

Exit criteria:

- Subagent isolation can be used for write-heavy tasks safely.

## Recommended Implementation Order

1. Phase 1 and Phase 2 first. Force planning is the safety boundary for all
   later subagent write workflows.
2. Phase 3 before background subagents. Without a queue/control plane,
   mid-flight steering and task notifications will become adapter-specific.
3. Phase 5 before Phase 6. Native spawn semantics should be deterministic and
   replayable before adding background concurrency.
4. Phase 7 after native spawn and mailbox, because coordinator behavior depends
   on reliable worker lifecycle.
5. Finish each phase with a dedicated quality pass: run focused `pylint`, fix
   real code issues, and avoid broad `disable` usage as a substitute for
   refactoring.

## Risks And Mitigations

- Risk: plan mode becomes annoying for read-only research.
  Mitigation: policy modes and task classifiers; default to required only for
  writes/risky tools/subagent spawn.
- Risk: duplicate child runs after resume.
  Mitigation: parent checkpoint before scheduling, idempotency keys, child row
  inserted before execution.
- Risk: steering messages corrupt model context.
  Mitigation: separate control requests from user messages; queue item kind
  decides whether it becomes prompt input.
- Risk: approval state leaks across sessions.
  Mitigation: approved prompts and plan approvals scoped to run/thread unless
  explicitly persisted by host policy.
- Risk: background children finish after parent moved on.
  Mitigation: mailbox notification with parent run/thread routing and bounded
  merge semantics.

## Documentation Updates Needed

- Done: `docs/roadmap.md` points to this plan from the cross-phase
  OpenClaude improvement workstream note.
- Done: [Planning and control](planning-and-control.md) records policy
  boundaries, mode reminders, clarification, steering, and subagent behavior.
- Done: [Runtime overview](runtime.md) records the runtime boundary, tool loop,
  stores, events, and replay concepts.
- Done: [Chat demo](chat-demo.md) records Phoenix tracing and concept checks.
- Add SDK recipes for:
  plan approval;
  mid-run steering;
  background child continuation;
  stopping a child.

## Definition Of Done

This initiative is complete when:

- Planning approval is durable, editable, resumable and enforced by runtime
  policy for configured task classes.
- Hosts can steer runs through typed control requests and durable queue events.
- `agent_tool`, `send_message_tool` and `task_stop_tool` are native runtime
  behaviors, not only intent payloads.
- Background subagents can be spawned, continued, stopped and observed.
- Coordinator profile can fan out research, synthesize results and drive
  implementation/verification with replayable events.
- Offline tests cover contracts, runtime transitions, stores and adapters; live
  evals cover at least one OpenRouter/OpenAI-compatible lane with plan approval
  and one subagent fan-out lane.
