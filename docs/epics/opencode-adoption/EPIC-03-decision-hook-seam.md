# EPIC-03 — Decision-hook seam on the gate chain (S)

Status: **DONE (2026-08-22)**. Track: [opencode-adoption](README.md). Source idea:
opencode's `permission.ask` plugin hook — a host callback that sees a resolved
permission decision and can override it (allow / ask / deny) without forking the runtime.

## What was built

A host-registered, ordered `ToolDecisionHook` seam that runs **last** in the per-call
policy resolution, after both the static `evaluate_tool_policy` and the dynamic
`ToolGate`. It lets a consumer (excel-ai / Zion / PentestLens) inject domain governance
into the runtime without patching it.

Hard invariants:

- **Tighten-only.** A hook may move the decision UP the strictness ladder
  `allow (0) < interrupt (1) < deny (2)` and no further. A looser proposal is ignored, so
  a hook can never un-block what static policy or the gate already denied, nor bypass
  either — it composes *on top of* them, never around.
- **Fail-closed.** A hook that raises is treated as a `deny` (with a
  `decision hook error: <ExcType>` reason). A broken governance hook blocks the call; it
  never silently allows it.
- **Ordered + monotonic.** Hooks run in registration order and each sees the
  decision as tightened by the ones before it.
- **Steering feedback.** A hook may attach optional `feedback` text. On a tightened call
  it is folded into the model-facing reason (the DENY path surfaces only `reason` as the
  blocked envelope's error message, so feedback kept only in metadata would be lost) and
  also mirrored to `policy.metadata["decision_hook_feedback"]` for programmatic hosts.

## Surface

- `agent_driver/tools/policy/decision_hooks.py` (new):
  - `ToolDecisionHook` — `Protocol`, `__call__(*, tool_name, args, manifest, run_input,
    decision, reason) -> ToolDecisionHookResult | None`. Return `None` to abstain.
  - `ToolDecisionHookResult(decision, reason=None, feedback=None)` — frozen slots dataclass.
  - `tighten_decision(current, proposed)` / `apply_decision_hooks(hooks, ...)` — pure,
    unit-tested helpers.
  - Re-exported from `agent_driver.tools.policy` (public governance path).
- `RunnerConfig.tool_decision_hooks: tuple[ToolDecisionHook, ...]` (default `()`), threaded
  through `sdk.factory.create_agent` into `GovernedToolExecutor(decision_hooks=...)`.
- `GovernedToolExecutor._resolve_call_policy` invokes the hooks after the gate and, when the
  decision changes, `model_copy`s the `ToolPolicyOutcome` (decision + folded reason +
  feedback metadata). No-op when no hooks are registered.

Behaviour-neutral by default (empty tuple → the resolution path is byte-for-byte unchanged).

Tests: `tests/tools/test_tool_decision_hooks.py` — pure `apply_decision_hooks` (tighten
wins, loosen ignored, `None` abstains, raising fails closed to DENY, feedback carried,
hooks compose monotonically) + end-to-end through `GovernedToolExecutor` (an allow→deny
hook blocks a call the static policy allowed, the handler never runs, the envelope is
`policy_denied`, and the feedback reaches the model via the error message; empty-hooks and
abstaining-hook both allow). Broad `tools`/`runtime`/`contracts`/`sdk` sweep green.

## Not done (deliberately)

- **Schema rewrite** — opencode's companion `tool.definition` hook, which rewrites a tool's
  model-facing description/params. That is a catalog-assembly concern (it changes what the
  model *sees*, not what it's *allowed to do*) and belongs with progressive tool-catalog
  disclosure (EPIC-09), not the decision gate. Deferred.
- **No new HITL surface** — the seam only re-decides among the existing
  allow/interrupt/deny outcomes; an `interrupt` from a hook parks on the normal approval
  machinery. Pairs naturally with EPIC-04 (correcting-rejection feedback).
