# Force Planning

Force planning is the runtime policy that requires an approved plan before an
agent performs configured risky work. It is separate from ordinary todo
tracking:

- `todo_write` and `planning_state_update` are live progress tools;
- `PlanArtifact` and `PlanApprovalPayload` are approval artifacts;
- force-planning policy decides whether a tool call can execute before an
  approved artifact exists.

## Policy Input

The typed policy contract is `PlanningPolicyInput` in
`agent_driver.contracts.context.planning`. Hosts embed it in
`ToolPolicyInput.metadata["force_planning"]`.

Supported modes:

- `off`: no force-planning gate;
- `prompt_only`: prompt guidance only, no runtime denial;
- `required_for_writes`: gate write/external-action tools;
- `required_for_risky_tools`: gate tools at or above the configured risk
  threshold;
- `always_for_multistep`: gate once expected step count crosses the configured
  threshold.

Legacy metadata is still accepted:

- `force_planning_enabled=true`;
- `planning_hint_enforce=true` with a required `planning_hint`.

## Adaptive Planning Hint

`agent_driver.runtime.planning_policy.classify_planning_hint(...)` provides a
deterministic hint for Claude Code-like behavior:

- simple/direct tasks should skip plan mode;
- research-only tasks should usually skip plan mode;
- non-trivial implementation can suggest plan mode;
- runtime safety boundaries return `required`.

The runtime also classifies planned tool batches. A required hint can be derived
from:

- side-effecting tools;
- planned `agent_tool` spawn;
- expected step count of four or more.

Hints are advisory unless a host opts into enforcement with
`planning_hint_enforce=true`.

## Gate Behavior

The governed tool policy evaluator checks force planning before tool execution.
Planning tools are exempt so the model can enter plan mode and submit a plan.

The gate allows execution when the policy contains an approved plan marker:

- `approved=true`;
- `approved_plan_id`;
- `approved_plan.plan_id`;
- `approved_plan.approved=true`.

When the gate blocks execution, the model-visible tool result contains
structured remediation with `error_kind="force_planning_required"`. The
assistant should respond by entering plan mode, producing an approval artifact,
and retrying after the host approves.

## Approval Flow

`exit_plan_mode_v2` produces plan content and can trigger a
`plan_approval_required` interrupt. On approve or edit-resume, the runtime:

- records approved plan metadata in run output;
- updates tool-policy metadata so later side-effecting tools in the same run
  can proceed;
- keeps the approval scoped to the current run/thread unless the host
  explicitly persists it elsewhere.

Reject and cancel remain terminal HITL decisions for that pending approval.

## Chat-Demo Defaults

The public chat-demo UI hides raw planning controls and filesystem/shell tools.
Planning remains always available inside the runtime through the planning tool
pack and is surfaced through:

- plan approval cards;
- planning snapshots;
- force-planning denied tool cards;
- replay events.

Backend configuration:

- `CHAT_DEMO_FORCE_PLANNING`;
- `CHAT_DEMO_FORCE_PLANNING_MODE` or `CHAT_DEMO_PLANNING_MODE`.

Current product default should remain `required_for_writes` when force planning
is enabled. It protects write/external side effects without forcing plan
approval for read-only research.

## Testing

Local deterministic coverage should include:

- policy evaluator allows planning tools while force planning is enabled;
- side-effecting tools are denied without an approved plan;
- side-effecting tools are allowed after approval;
- `prompt_only` does not gate;
- planned tool hints can opt into enforcement;
- plan approval resume marks force planning approved.

Chat-demo verification should include:

- backend fake scenario `force_planning_block`;
- replay contains denied tool calls and remediation;
- browser smoke confirms the policy-denied replay card remains visible after
  frontend changes.

## Remaining Work

- Add durable `PlanArtifact` persistence beyond process-local helpers.
- Emit dedicated plan lifecycle runtime events.
- Add checkpoint/reload tests for awaiting plan approval after durable store
  reload.
- Gate native subagent spawn once `agent_tool` becomes a runtime spawn surface.
