# Testing

Keep checks focused on the code touched by the current slice, then add a live
chat-demo check when the behavior is visible to users.

## Unit And Runtime Tests

Common local commands:

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/python -m pytest tests/tools/test_planning_tools.py -q
.venv/bin/python -m pytest tests/runtime/test_tool_stage_protocol.py -q
```

Use narrow test files while iterating, then broaden when a change touches shared
runtime, tools, providers, or UI event projection.

## Formatting And Quality

Use the repository's configured tools:

```bash
.venv/bin/python -m black --check agent_driver tests
.venv/bin/python -m isort --check-only agent_driver tests
.venv/bin/python -m pylint agent_driver/tools/planning.py
```

For phase-end quality passes, run `pylint` over touched runtime/domain modules.
Prefer real refactoring over broad `disable` pragmas. If an old module has
pre-existing style warnings outside the current slice, fix only the relevant
ones unless a broader refactor is explicitly planned.

## Chat Demo Browser Checks

For user-visible concepts, verify on the real React UI:

```bash
make test-chat-concepts CHAT_DEMO_URL=http://localhost:5174
```

Useful single scenarios:

```bash
.venv/bin/python examples/chat-demo/frontend/tests/e2e/chat_concepts_smoke.py \
  --scenario web-search-final
.venv/bin/python examples/chat-demo/frontend/tests/e2e/chat_concepts_smoke.py \
  --scenario subagent-final
```

When the issue is nondeterministic or model-dependent, reproduce it with the
live provider and inspect Phoenix traces.

For live model-dependent chat behavior, use the Phoenix-backed probe. It starts
real chat runs, captures `x-run-id`, fetches
`/api/chat/runs/{run_id}/trace-summary`, and stores failed artifacts under
`/tmp/chat-demo-live`:

```bash
CHAT_DEMO_URL=http://localhost:5174 \
  .venv/bin/python examples/chat-demo/frontend/tests/e2e/chat_live_probe.py --all
```

The current suite covers direct answers, web research, plan-only behavior,
deliverable-no-replan, clarification avoidance, web-search final answers,
subagent synthesis, and mid-run steering.

For research/provider slices, also inspect the Phoenix trace and
`/api/chat/runs/{run_id}/trace-summary` after each live probe. Check that the
run has a terminal event, fetched evidence before synthesis when required,
source links or source shelf coverage, no unknown tools, no progress-only final,
and no unfinished visible todos. Treat repeated trace failures as a runtime
contract issue before adding heavier orchestration.

For model-specific OpenRouter failures, follow
[Provider and model debugging](provider-model-debugging.md): check current
OpenRouter docs, capture Phoenix/trace artifacts, and record the model matrix
result instead of leaving the finding only in chat history.

## Live Provider Checks

Live tests are opt-in and should load secrets from `.env` without printing
them:

```bash
AGENT_DRIVER_RUN_LIVE_TESTS=1 .venv/bin/python -m pytest -m live tests
```

Prefer deterministic fake-provider tests for CI-style regressions. Use live
provider checks to validate product behavior, prompt quality, and tracing.
