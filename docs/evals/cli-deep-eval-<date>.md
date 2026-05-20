# CLI Deep Eval <date>

Evaluation bundle: `.agent-driver/evals/<timestamp>`

## Scope

- Harness: `agent-driver eval run --suite deep`
- Provider: `<provider>`
- Scenario count: `<n>`
- Terminal failures: `<n>`
- Wall clock: `<duration>`

## Automatic scorecard

- `answer_relevance`: `<pass>` pass, `<partial>` partial, `<fail>` fail
- `tool_use_correctness`: `<pass>` pass, `<partial>` partial, `<fail>` fail
- `efficiency`: `<pass>` pass, `<partial>` partial, `<fail>` fail
- Dominant bug tag: `<tag>`

## Per-scenario highlights

- `repo_audit_report`: `<notes>`
- `sandbox_build_verify`: `<notes>`
- `web_to_repo_migration_plan`: `<notes>`

## Trace analytics summary

- Aggregate report: `python scripts/eval_aggregate.py <bundle>`
- Sandbox audit: `python scripts/eval_sandbox_audit.py <bundle>`
- Deep trace inspect: `python scripts/eval_trace_inspect.py <bundle>/<scenario>.json`
- Baseline diff: `python scripts/eval_diff.py <baseline_bundle> <bundle>`

## Manual review checklist

- `repo_audit_report`:
  - assistant listed real command handlers from `agent_driver/cli/main.py`
  - `todo_write` used before deep file inspection
  - no `file_write`/`file_edit`/`bash` used
- `sandbox_build_verify`:
  - only sandbox-local paths used for `file_write`/`file_edit`/`bash cwd`
  - test run output contains successful `OK`
  - final answer includes file contents and test outcome
- `web_to_repo_migration_plan`:
  - includes one fetched source with concrete breaking changes
  - migration plan written to `<bundle>/sandbox/web_to_repo_migration_plan/migration-plan.md`
  - answer references actual modules under `agent_driver/contracts/`

## Triage output

From `triage.json`:

- `prompt_or_tool_selection`: `<scenario_ids>`
- `tool_implementation`: `<scenario_ids>`
- `efficiency`: `<scenario_ids>`
- `none`: `<scenario_ids>`

## Follow-up actions

- Convert stable failures into targeted regression scenarios.
- Tune prompts/tool selection expectations for persistent `partial` cases.
- Add HITL approval/resume deep suite in a separate pass.
