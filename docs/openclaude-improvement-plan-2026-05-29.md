# OpenClaude Improvement Plan: Force Planning, Steerability, Subagents

Дата анализа: 2026-05-29.

Цель: усилить `agent-driver`, взяв лучшие архитектурные идеи из
`/home/roman/pyprojects/ML/openclaude` в трех направлениях:

- force planning: обязательное планирование до выполнения рискованных или
  многошаговых задач;
- steerability: возможность направлять модель во время диалога и выполнения;
- subagents: управляемые дочерние агенты, команды и параллельная оркестрация.

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
| 2 | in progress | Force planning policy engine | Runtime gate for risky tools/subagent spawn |
| 3 | pending | Steering contracts and queue | `ControlRequest` + durable command queue |
| 4 | pending | Steering adapters | SSE/SDK/chat-demo control APIs |
| 4a | pending | Optional Instructor spike | Pydantic-validated structured extraction adapter |
| 5 | pending | Native subagent spawn | `agent_tool` schedules durable child runs |
| 6 | pending | Background subagents + mailbox | async children, task notifications, mailbox |
| 7 | pending | Coordinator profile | coordinator/worker prompts and evals |
| 8 | pending | Isolation and advanced backends | worktree/cwd isolation, artifact handoff |

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

Next Phase 2 slice:

- Extend the same gate to native subagent spawn once `agent_tool` becomes a
  runtime scheduling surface.
- Add a replay/UI polish pass for policy-denied tool cards so remediation is
  visually distinct from the raw denial reason.
- Extend `planning_hint` from request-text rules to planned tool batches
  (side-effecting tools, native `agent_tool`, estimated step count) so the
  same contract can drive runtime-required planning outside chat-demo.
- Pick and document the product default for chat-demo
  (`prompt_only` vs `required_for_writes`) after live demo checks.

## Periodic Product Checks

Backend-only completion is not enough for this workstream. Every phase must
include a chat-demo integration checkpoint:

- expose the new runtime concept through `examples/chat-demo/backend` when it
  affects users;
- render or operate the concept in `examples/chat-demo/frontend`;
- run targeted backend/frontend tests;
- start the demo locally and verify the main user path with Playwright;
- capture at least one screenshot or DOM assertion for the changed surface;
- document any deferred UI gap in this file before moving to the next phase.

Current demo-gate status:

- Python Playwright installed in the repo `.venv`; Chromium browser installed.
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
  reconnect/replay.
- Phase 5-6: subagent spawn, background status, mailbox notifications,
  continue and stop are visible in chat-demo.
- Phase 7: coordinator/worker mode is selectable and shows worker lifecycle.

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
- [ ] Add SQLite or durable plan artifact persistence beyond process-local
  helpers.
- [ ] Emit dedicated plan lifecycle runtime events:
  `plan_mode_entered`, `plan_artifact_updated`, `plan_approval_requested`,
  `plan_approved`, `plan_rejected`.
- [ ] Add checkpoint/resume tests for awaiting plan approval after process
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
- [ ] Gate native subagent spawn once `agent_tool` becomes a runtime spawn
  surface.
- [ ] Run and document a current Playwright smoke for chat-demo force-planning
  policy-denied replay after the latest design changes.
- [ ] Decide and document chat-demo default mode:
  `prompt_only` or `required_for_writes`.

### Phase 3: Steering Contracts And Queue

- [ ] Add `agent_driver/contracts/control.py` with `ControlRequest`,
  `ControlResponse`, and `CommandQueueItem`.
- [ ] Add command queue stores:
  in-memory first, SQLite second.
- [ ] Add control dispatcher with priority semantics:
  `now > next > later`, FIFO within priority.
- [ ] Add SDK methods:
  `control`, `enqueue`, `set_model`, `set_permission_mode`,
  `cancel_queued_message`.
- [ ] Drain queue at runtime step boundaries.
- [ ] Emit typed control/queue runtime events.
- [ ] Add tests for priority, FIFO, cancellation, checkpoint/resume, and
  `set_model` affecting the next LLM request.

### Phase 4: User Steering UX Adapters

- [ ] Extend SSE projection for control/queue events.
- [ ] Add chat-demo/backend control endpoints.
- [ ] Add chat-demo/frontend controls for enqueue/cancel/interrupt/model
  switch where product-appropriate.
- [ ] Persist steering operations in session transcript/history.
- [ ] Add replay view support for queued messages and controls.
- [ ] Verify mid-run steering with Playwright and record screenshot/DOM
  assertion.

### Phase 4a: Optional Instructor Spike

- [ ] Add optional dependency extra without affecting default installs.
- [ ] Add `agent_driver/structured/` adapter boundary.
- [ ] Prototype one steering parser into typed `ControlRequest`.
- [ ] Prototype one plan artifact validator.
- [ ] Surface validation/retry failures as structured runtime observations or
  errors.

### Phase 5: Native Agent Tool Spawn

- [ ] Make `agent_tool` a runtime-recognized spawn request.
- [ ] Convert tool envelopes into `SubagentGroupSpec`.
- [ ] Persist group before child execution with idempotency keys.
- [ ] Pass subagent event callback through sync execution.
- [ ] Add native `task_stop_tool`.
- [ ] Add `send_message_tool` continuation semantics for existing child
  context.
- [ ] Add tests for spawn, resume idempotency, continuation, stop, and events.

### Phase 6: Background Subagents And Mailbox

- [ ] Add `asyncio_background` subagent backend.
- [ ] Add durable mailbox for messages, permissions, plan approvals, and task
  notifications.
- [ ] Queue child-to-parent notifications as `later` commands.
- [ ] Add status polling and collection APIs.
- [ ] Propagate parent abort to children.
- [ ] Add budgets/backpressure for child/group scheduling.

### Phase 7: Coordinator Profile

- [ ] Add coordinator profile/config.
- [ ] Add coordinator prompt snapshot based on OpenClaude principles.
- [ ] Add worker definitions: `worker`, `researcher`, `implementer`,
  `verifier`.
- [ ] Restrict coordinator/worker tool surfaces.
- [ ] Add scratchpad/artifact handoff rules.
- [ ] Add evals for research fan-out, corrected continuation, and verifier
  catch.

### Phase 8: Isolation And Advanced Backends

- [ ] Add worktree isolation for child runs.
- [ ] Add cwd override with policy validation.
- [ ] Evaluate process backend after `asyncio_background`.
- [ ] Add bounded artifact refs for child outputs.
- [ ] Add cleanup tests for completed/cancelled children.

### Documentation And Recipes

- [ ] Update `docs/roadmap.md` with a pointer to this plan.
- [ ] Add `docs/architecture/force-planning.md`.
- [ ] Add `docs/architecture/steering-control-plane.md`.
- [ ] Extend `docs/architecture/multi-agent-orchestration.md`.
- [ ] Add SDK recipes for plan approval, mid-run steering, child continuation,
  and stopping a child.

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

- Update `docs/roadmap.md` with a short pointer to this plan.
- Add `docs/architecture/force-planning.md`.
- Add `docs/architecture/steering-control-plane.md`.
- Extend `docs/architecture/multi-agent-orchestration.md` with mailbox,
  background execution and native `agent_tool` semantics.
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
