# Epic 010 lifecycle hook surface inventory

Дата: 2026-07-03.

Статус: deterministic thin slice implemented.

## Existing surfaces

| Surface | Current implementation | 010 treatment |
|---|---|---|
| Tool hooks | `agent_driver/contracts/hooks.py` exposes `ToolHook`, `HookResponse`, transform, `prevent_continuation`, `additional_context` and optional per-hook timeout. | adapt: bridged by `result_from_existing_hook_output` and `LifecycleMiddlewareAuditExecutor` without changing tool execution semantics. |
| Run lifecycle hooks | `agent_driver/runtime/lifecycle_hooks.py` exposes run start, before/after LLM, finalize, tool evidence and error callbacks. | adapt: `RevisionRequest` and finalize-like outputs map to typed lifecycle verdicts. |
| Hook chains | `agent_driver/contracts/hook_chains.py` and `agent_driver/runtime/hook_chains.py` model fallback spawning with cooldown, dedup and depth state. | adapt: fallback specs map to `spawn_fallback` verdict metadata. |
| Runtime events | `agent_driver/contracts/events.py` and runtime stream projection provide canonical event logs. | adapt: added lifecycle hook runtime event kinds for started/completed/blocked/failed/timed-out rows. |
| Adapter plane | 009 adapter projection already exposes compact event rows, artifact refs and compatibility reports. | adapt: lifecycle hook audit rows project to synthetic `HarnessAdapterEvent` rows. |
| Capability packs and continuous validation | 008 capability packs, evidence indexes and release-gate policies are deterministic/no-live by default. | adapt: lifecycle hook scenarios, artifact types and release-gate policy were added. |

## Product hook-like logic

| Product | Hook-like behavior | 010 fixture status |
|---|---|---|
| Excel AI | workbook context checks, edit/transaction approval, chart/report artifact evidence, no-progress finalization | modeled as deterministic lifecycle hook registration/audit/report fixtures; behavior remains metadata-only observe mode. |
| chat-demo deep research | source-evidence requirements, report artifact checks, workspace write policy, steering/interrupts, fallback agents | modeled as deterministic lifecycle hook registration/audit/report fixtures; live provider/Phoenix/UI evidence remains no-claim. |

## Reference feature decisions

| Reference signal | Decision | Reason |
|---|---|---|
| Claude Code deterministic hooks | inspired-by | Useful for lifecycle vocabulary and blocking semantics; this slice keeps blocking opt-in. |
| OpenClaude hook event schemas | inspired-by | Event vocabulary informed session/compact/subagent/file-change shapes. |
| Microsoft Agent Framework middleware/context providers | inspired-by | Confirms middleware as a first-class extension plane. |
| LangGraph interrupts | adapt | Approval/interrupt rows are JSON-safe and replayable before durable resume expansion. |
| Unbounded plugin system | reject | 010 remains registration/audit metadata, not arbitrary plugin loading. |
| Fail-closed defaults | reject | Observe/warn failures continue; enforce blocks only with explicit policy. |
| Live provider/Phoenix/Playwright gates | defer | Defined as no-claim/optional in deterministic thin slice. |
