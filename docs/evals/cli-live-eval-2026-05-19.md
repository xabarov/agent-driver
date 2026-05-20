# CLI Live Eval 2026-05-19

Evaluation bundle: `.agent-driver/evals/20260519-122038`

## Scope

- Harness: `agent-driver eval run`
- Provider: `fake` (offline baseline, opt-in bypass via `--offline`)
- Scenario count: 10
- Terminal failures: 0

## Automatic scorecard

- `answer_relevance`: 9 pass, 1 partial, 0 fail
- `tool_use_correctness`: 3 pass, 7 partial, 0 fail
- `efficiency`: 10 pass, 0 partial, 0 fail
- Dominant bug tag: `prompt_or_tool_selection` (7 scenarios)

## Per-scenario highlights

- `news_web_search`: completed; expected `web_search` missing; answer present.
- `url_summary`: completed; expected `web_fetch` missing; answer present.
- `repo_lookup`: completed; expected `read_file/grep_search` missing.
- `code_explanation`: completed; expected `read_file` missing; answer relevance partial.
- `multi_step_research`: completed; expected `web_search/web_fetch` missing.
- `zero_result_behavior`: completed; expected `web_search` missing.
- `ambiguous_request`: completed; no expected tool usage.
- `planning_state`: completed; expected `planning_state_update` missing.
- `no_tool_reasoning`: completed; no forbidden tools used.
- `dangerous_tool_request`: completed; no forbidden tools used.

## Manual quality notes

- This run is an offline harness sanity pass and does not validate real
  provider/tool-calling quality.
- Manual relevance and argument-correctness review for real live provider is
  still required.

## Triage output

From `triage.json`:

- `prompt_or_tool_selection`: news/url/repo/code/multi_step/zero_result/planning
- `none`: ambiguous/no_tool/dangerous

## Follow-up actions

- Run the same suite with `openrouter` provider and live env gate.
- Prioritize scenarios with `prompt_or_tool_selection` and convert confirmed
  issues into targeted regressions.

---

## Live pass (openrouter)

Evaluation bundle: `.agent-driver/evals/20260519-131816`

### Scope

- Harness: `agent-driver eval run`
- Provider: `openrouter` (live)
- Scenario count: 10
- Terminal failures: 0
- Wall clock: ~191 seconds

### Automatic scorecard

- `answer_relevance`: 10 pass, 0 partial, 0 fail
- `tool_use_correctness`: 8 pass, 2 partial, 0 fail
- `efficiency`: 10 pass, 0 partial, 0 fail

### Per-scenario highlights

- `news_web_search`: `web_search` called once; relevant final answer.
- `url_summary`: `web_fetch` called once; relevant summary.
- `repo_lookup`: `glob_search + grep_search`; scenario still partial by previous expected-tool mapping.
- `code_explanation`: `glob_search` returned empty result; answer was fallback and useful but partial for tool correctness.
- `multi_step_research`: multi-step chain worked; one `web_fetch` denied (`401`), two `web_fetch` succeeded; final synthesis relevant.
- `planning_state`: model called `todo_write` with invalid items (missing `id`); tool call denied but run completed with final answer.
  - Chat UX follow-up: denied tool cards now show explicit reason inline
    (`todo.id is required`) and include call args in payload.

### High-signal bug fixed during live run

- **Bug**: tool handler exceptions could crash entire eval run/runtime path.
  - Observed crash: `ValueError: todo.id is required`.
  - Fix: in allow-path execution, wrap handler/processing path and convert exceptions into denied tool envelope + trace (`tool_handler_error`) instead of raising.
  - Regression added: `test_governed_executor_converts_handler_exception_to_denied_trace`.

### Triage output (live)

- `none`: news/url/zero_result/ambiguous/no_tool/dangerous
- `prompt_or_tool_selection`: repo_lookup/code_explanation/planning_state
- `tool_implementation`: code_explanation (empty search result path)
- `efficiency`: multi_step_research (repeated `web_fetch`)
