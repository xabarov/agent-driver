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

## 5) Secrets and safety

- Eval artifacts are written through redaction helper for sensitive keys and
  common secret-like string patterns.
- `.agent-driver/` is git-ignored by default.
- Do not paste raw provider credentials into prompt text.
