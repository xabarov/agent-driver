# Lifecycle hooks and deterministic middleware extension plane

Дата создания: 2026-07-03.

Статус: implemented deterministic thin slice.

Workstream: cross-workstream W3/W4/W5/W7 after epic 009. Builds on existing
tool hooks, run lifecycle hooks, hook chains, runtime decisions, adapter
projection, capability packs and continuous-validation audit.

## Scope result

010 introduces a shared lifecycle middleware metadata plane without replacing
existing hook APIs or changing default runtime behavior. The implemented slice
adds typed contracts, deterministic audit execution, adapter projection,
product fixtures, capability-pack scenarios and release-gate policy. Live
provider, Phoenix and Playwright probes remain defined as no-claim unless a
caller explicitly executes those gates.

## Implementation evidence

| Area | Evidence |
|---|---|
| Hook inventory | `docs/backlog/agent-driver-harness-analysis/notes/2026-07-03-epic-010-lifecycle-hook-surface-inventory.md` |
| Contracts | `agent_driver/contracts/lifecycle_hooks.py` |
| Runtime audit executor | `agent_driver/runtime/lifecycle_middleware.py` |
| Runtime event vocabulary | `RuntimeEventType.LIFECYCLE_HOOK_*` |
| Adapter projection and product fixtures | `agent_driver/harness/lifecycle_hooks.py` |
| Capability scenarios | `lifecycle_hooks.tool_transform_audit.v1`, `lifecycle_hooks.approval_interrupt_audit.v1`, `lifecycle_hooks.excel_workbook_policy.v1`, `lifecycle_hooks.chat_demo_research_policy.v1` |
| Evidence artifact types | `lifecycle_hook_compatibility_report`, `lifecycle_hook_audit` |
| Release gate policy | `lifecycle_hook_api_change` |
| Tests | `tests/contracts/test_lifecycle_hook_contracts.py`, `tests/runtime/test_lifecycle_middleware.py`, `tests/harness/test_lifecycle_hooks.py` |

## Completed checklist

### Phase A. Hook surface inventory

- [x] Inventory `ToolHook` call sites and current pre/post tool-use behavior.
- [x] Inventory `RunLifecycleHook` call sites: run start, before/after LLM,
  finalize, tool evidence and error.
- [x] Inventory `HookChainExecutor` fallback decisions and host spawning paths.
- [x] Inventory existing runtime decision, policy, supervision and adapter event
  rows that already represent hook-like decisions.
- [x] Inventory Excel AI hook-like local logic as deterministic fixtures:
  workbook context checks, approvals, edit/transaction gates, chart/report
  artifact checks and no-progress recovery.
- [x] Inventory chat-demo hook-like local logic as deterministic fixtures:
  source requirements, report artifact checks, workspace write policy,
  steering/interrupts and fallbacks.
- [x] Mark each reference feature as `copy`, `adapt`, `inspired-by`, `defer`
  or `reject`.

### Phase B. Contract and vocabulary

- [x] Add lifecycle hook event/result/registration/chain/audit/compatibility
  contracts.
- [x] Add event and verdict vocabularies with strict JSON/redaction validation.
- [x] Add mode vocabulary: `observe`, `warn`, `enforce`, `disabled`.
- [x] Add failure policy vocabulary: `continue`, `skip_remaining`,
  `block_if_enforce`, `fail_run`.
- [x] Add deterministic ordering rules and timeout defaults.
- [x] Add contract tests for secret-shaped metadata, invalid verdicts,
  non-JSON payloads, missing hook ids and invalid event subscriptions.

### Phase C. Middleware audit executor

- [x] Implement a deterministic audit wrapper that records
  hook-started/hook-completed/hook-failed/hook-timed-out rows around existing
  hook calls.
- [x] Bridge existing `HookResponse.prevent_continuation` and
  `additional_context` into `LifecycleHookResult`.
- [x] Bridge `RevisionRequest`, `FinalizeNow` and fallback specs into typed
  lifecycle verdicts without changing runtime behavior.
- [x] Record elapsed time, timeout, exception class, selected verdict,
  continuation behavior and redaction status.
- [x] Add tests proving observe/warn hook failures do not break runs and
  enforce hook blocks only when explicitly configured.
- [x] Add tests proving transformed tool calls still require guardrails.

### Phase D. Runtime event and adapter projection

- [x] Add compact runtime events for lifecycle hook outcomes.
- [x] Project hook audit rows into 009 `HarnessAdapterEvent` rows where useful.
- [x] Include hook audit summaries in compatibility/support-bundle-ready
  evidence metadata.
- [x] Add replay fixture showing hook rows are stable from event logs.
- [x] Add no-claim states for lifecycle events that a host/protocol does not
  support.
- [x] Keep UI changes out of the first slice; Playwright evidence is no-claim.

### Phase E. Policy and approval integration

- [x] Define how hook verdicts relate to existing policy/runtime decisions:
  observe/warn/enforce, block, request approval, finalization and revision.
- [x] Add approval-request hook event shape that can feed 009 adapter approval
  rows.
- [x] Add interrupt-requested and approval-resolved hook event shapes as
  deterministic records before durable resume expansion.
- [x] Add side-effect metadata requirements for hooks that touch filesystem,
  workbook edits, workspace writes or external services.
- [x] Add tests for approval requested/resolved/missing-host-ui no-claim paths
  through deterministic report fixtures.

### Phase F. Host validation: Excel AI

- [x] Add a deterministic Excel AI lifecycle hook compatibility report.
- [x] Model workbook context check, edit/transaction approval and chart/report
  artifact check as lifecycle hook events/results or fixtures.
- [x] Prove hook audit rows can be consumed without Excel-specific upstream
  vocabulary via adapter-safe projection.
- [x] Keep behavior metadata-only unless a product owner explicitly enables
  warn/enforce mode.
- [x] Capture Playwright screenshots only if Excel UI hook rows are changed:
  no UI rows changed, so the gate remains no-claim.

### Phase G. Host validation: chat-demo deep research

- [x] Add a deterministic chat-demo lifecycle hook compatibility report.
- [x] Model source-evidence requirement, report artifact check, workspace write
  policy and steering/interrupt events as lifecycle hook events/results or
  fixtures.
- [x] Prove hook audit rows can be consumed by deep research traces/support
  bundles without product-local event parsing via adapter-safe projection.
- [x] Keep live research/provider/Phoenix evidence skipped/no-claim unless
  explicitly executed.
- [x] Capture Playwright screenshots only if chat-demo UI hook rows are changed:
  no UI rows changed, so the gate remains no-claim.

### Phase H. Capability pack and continuous-validation integration

- [x] Add capability-pack scenarios:
  `lifecycle_hooks.tool_transform_audit.v1`,
  `lifecycle_hooks.approval_interrupt_audit.v1`,
  `lifecycle_hooks.excel_workbook_policy.v1`,
  `lifecycle_hooks.chat_demo_research_policy.v1`.
- [x] Add deterministic evidence indexes for hook compatibility reports.
- [x] Wire hook compatibility reports into 008 audit as adapter/runtime
  evidence through accepted artifact types and release-gate policy.
- [x] Add release-gate policy rules: lifecycle hook API changes require
  contract tests and replay fixtures; enforce-mode changes require host
  adoption evidence; UI changes require Playwright; live runtime claims require
  Phoenix.

### Phase I. Optional live/UI probes

- [x] Define but do not require a cheap live hook trace smoke that emits at
  least one run-start, tool, approval/interrupt or finalize hook row through
  deterministic scenario ids.
- [x] Define Phoenix span expectations for live hook/runtime claims as
  `phoenix_trace` no-claim until explicit execution.
- [x] Define Playwright screenshot expectations for hook rows in Excel AI and
  chat-demo UI if UI changes are made.
- [x] Record skipped live/UI gates as `skipped`, `stale` or `no_claim` through
  existing continuous-validation policy.

## Verification

Command:

```bash
uv run python -m pytest tests/contracts/test_lifecycle_hook_contracts.py tests/runtime/test_lifecycle_middleware.py tests/harness/test_lifecycle_hooks.py tests/contracts/test_public_exports.py tests/harness/test_adapter_protocol.py tests/harness/test_capability_packs.py tests/harness/test_continuous_validation.py -q
```

Result: `42 passed`.
