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
