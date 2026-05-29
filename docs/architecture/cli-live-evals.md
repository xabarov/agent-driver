# CLI Live Evaluation Operator Guide

This guide documents the `agent-driver eval` workflow for repeatable CLI trace
audits with artifact bundles.

## 1) Run protocol

- Live runs are opt-in and require `AGENT_DRIVER_RUN_LIVE_CLI_EVALS=1`.
- For dry/offline baseline, use `--offline` with deterministic `fake` provider.
- Each scenario is bounded by defaults from the scenario set:
  - `max_steps=12`
  - `max_tool_calls=6`
  - `deadline_seconds=120`

Run full suite:

```bash
agent-driver eval run \
  --provider openrouter \
  --output-dir .agent-driver/evals
```

Run offline baseline:

```bash
agent-driver eval run \
  --provider fake \
  --offline \
  --output-dir .agent-driver/evals
```

If provider/env is not configured, eval exits cleanly with an explicit
`eval skip:` message.

## 2) Artifact layout

Artifacts are saved under:

```text
.agent-driver/evals/<timestamp>/
```

Bundle files:

- `manifest.json` - provider/model/store/scenario manifest
- `<scenario_id>.json` - full per-scenario artifact
- `summary.json` - structured summary list for all runs
- `report.md` - markdown quality scorecard
- `triage.json` - grouped bug tags for triage backlog

Per-scenario artifact includes:

- original prompt + expected/forbidden tools;
- run identifiers and terminal state;
- compact event replay timeline;
- tool trace;
- summary row with quality fields;
- final answer;
- redacted full `run_output`.

## 3) Inspecting artifacts

Summary-level inspect:

```bash
agent-driver eval inspect --summary-json .agent-driver/evals/<ts>/summary.json
```

Inspect single scenario timeline:

```bash
agent-driver eval inspect --artifact-json .agent-driver/evals/<ts>/news_web_search.json
```

Optional filter by scenario id:

```bash
agent-driver eval inspect \
  --summary-json .agent-driver/evals/<ts>/summary.json \
  --scenario-id zero_result_behavior
```

## 4) Scenario maintenance

Scenarios are defined in `agent_driver/cli/evals.py` via
`default_live_scenarios()`.

When adding a scenario:

- add unique `scenario_id` and prompt;
- define `expected_tools` and `forbidden_tools`;
- keep bounded limits (`max_steps`, `max_tool_calls`, `deadline_seconds`);
- add expected answer anchors in `expected_answer_contains` when relevant.

Deep suites can additionally use:

- `required_tools` for strict must-hit tool checks;
- `expected_tool_chain_contains` for sequence validation;
- `expected_min_tool_calls` for minimum action depth;
- `sandbox_required` + `prompt_template` for isolated per-scenario workspace.

## 5) Workspace context and path resolution

Eval harness now injects run metadata:

- `app_metadata.workspace_cwd`
- `app_metadata.eval_sandbox_dir`

Runner binds this into run-scoped tool context, so filesystem tools and default
shell cwd resolve relative paths against the run workspace instead of process
cwd.

Implications:

- Relative `read_file` / `file_write` / `glob_search` / `grep_search` paths are
  now valid in eval prompts.
- `agent-driver eval run` resolves `--output-dir` to absolute path before bundle
  creation.

## 6) Summary and analytics fields

`summary.json` rows include extended diagnostics:

- `required_tools_missing`
- `runtime_step_count`
- `actual_tool_chain`
- `expected_chain_satisfied`
- `min_tool_calls_satisfied`

Utility scripts consume these fields:

- `scripts/eval_aggregate.py` reports `runtime_step_count` stats
- `scripts/eval_diff.py` diffs `required_tools_missing` and runtime step deltas
- `scripts/eval_trace_inspect.py` prints `runtime_step_count` in scenario header

## 7) Multi-step ReAct loop control

Single-agent ReAct runtime now allows multi-round tool execution by default.

Behavior:

- After each tool stage, runtime returns to `llm_call` while tool results are
  still needed.
- `force_final_answer` + `tool_choice_override=none` are no longer set
  unconditionally after the first tool round.
- Forced final-answer mode is now conditional and activates only when:
  - run is near `max_tool_calls` budget;
  - run is near `max_steps` budget;
  - two latest tool calls repeat the same `(tool_name, args)` pair;
  - web zero-result guardrail is active (`web_search_zero_streak >= 2`).

Prompt/runtime alignment:

- ReAct profile always receives base system instruction covering:
  - step-by-step tool usage;
  - valid `todo_write` statuses (`pending|in_progress|completed|cancelled`);
  - workspace-relative path expectations.
- Chat mode appends chat-specific tool policy on top of base instruction.

## 8) Secrets and safety

- Eval artifacts are written through redaction helper for sensitive keys and
  common secret-like string patterns.
- `.agent-driver/` is git-ignored by default.
- Do not paste raw provider credentials into prompt text.
