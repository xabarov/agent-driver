# CLI Deep Eval 2026-05-19

Latest evaluation bundle: `.agent-driver/evals/20260519-145323`
Previous reference bundle: `.agent-driver/evals/20260519-141706`

## Iteration 4 changes

- Runtime ReAct loop no longer forces final answer after first tool round.
- Added conditional forced-final guardrails (near budgets, repeated tool args, web zero-result streak).
- Added base ReAct system policy for non-chat runs, including `todo_write` status contract and workspace path guidance.
- Strengthened `todo_write` manifest JSON schema with nested todo item fields and `status` enum.
- Updated filesystem/shell tool argument descriptions to reflect relative-path support via workspace cwd.
- Tweaked `repo_audit_report` prompt to require explicit final answer after `read_file`.

## Scope

- Harness: `uv run agent-driver eval run --suite deep`
- Provider: `openrouter` (live)
- Scenario count: 3
- Terminal failures: 1 (`max_steps_exceeded`)
- Wall clock: ~167 seconds
- Run notes: latest run completed without retry-level harness failures.

## Automatic scorecard (`20260519-145323`)

- `answer_relevance`: 2 pass, 1 partial, 0 fail
- `tool_use_correctness`: 2 pass, 0 partial, 1 fail
- `efficiency`: 3 pass, 0 partial, 0 fail
- Dominant bug tags: `efficiency` (3), `prompt_or_tool_selection` (1), `runtime_loop_or_limits` (1)

## Diff vs previous bundle (`20260519-141706`)

Command:

`python scripts/eval_diff.py .agent-driver/evals/20260519-141706 .agent-driver/evals/20260519-145323`

Observed deltas:

- `repo_audit_report` improved on tool correctness: `fail -> pass`.
- `repo_audit_report` now covers required chain (`todo_write -> glob_search -> grep_search -> read_file`).
- `web_to_repo_migration_plan` improved on tool correctness: `fail -> pass`; now reaches terminal `file_write` and completes.
- `sandbox_build_verify` remains `tool_use_correctness=fail`; still ends by `max_steps_exceeded`.

## Per-scenario highlights

- `repo_audit_report`:
  - `status=completed`, `runtime_step_count=13`
  - required tools fully hit (`required_tools_missing=[]`)
  - `tool_use_correctness=pass`
- `sandbox_build_verify`:
  - tool execution includes `todo_write`, `file_write`, and repeated `bash`
  - `bash` receives 3 denials (`statement separator ';' is not allowed`) before one successful readonly run
  - expected chain still not satisfied (`file_edit` and `read_file` missing), terminal `max_steps_exceeded`
  - `tool_use_correctness=fail`
- `web_to_repo_migration_plan`:
  - completes full research-to-write chain (`todo_write -> web_search -> web_fetch -> glob_search -> grep_search -> read_file -> file_write`)
  - no required tools missing
  - `tool_use_correctness=pass`

## Trace analytics summary

- Aggregate:
  - `python scripts/eval_aggregate.py .agent-driver/evals/20260519-145323`
  - `runtime_step_count` stats: `min=13, median=18, p90=19, max=19`
  - `repeated_tool_arguments` no longer shows empty-args false-positive pattern
- Trace inspect:
  - `python scripts/eval_trace_inspect.py .agent-driver/evals/20260519-145323/<scenario>.json`
  - confirms multi-round ReAct behavior across scenarios; loops are now bounded by configured limits instead of first-round forced final

## Detailed problem breakdown (remaining fail)

### 1) `sandbox_build_verify`: repeated bash denials consume step budget

- Symptom:
  - Scenario ends as `failed` with `terminal_reason=max_steps_exceeded` and `runtime_step_count=18`.
  - `bash` is denied 3 times with `statement separator ';' is not allowed`.
- Why this matters:
  - The model spends tool budget on syntactically disallowed shell patterns instead of executing verification and repair steps.
  - It reaches a successful `bash` only after repeated denials, leaving no budget to finish `file_edit -> bash -> read_file` closure.
- Probable root cause:
  - Prompt policy and scenario instructions still allow ambiguous "run tests/build" phrasing that encourages multi-command shell strings with `;`.
  - Runtime does not provide an explicit, corrective hint after repeated identical bash denials.

### 2) Expected-chain mismatch in terminal phase

- Symptom:
  - `expected_tools_missing=['file_edit', 'read_file']`.
  - Final answer claims tests passed and shows code, but trace does not include the required final readback step.
- Why this matters:
  - Tool-use correctness fails even when content quality is acceptable.
  - This is a deterministic contract miss, not a stochastic relevance issue.
- Probable root cause:
  - Scenario contract (`file_edit`, then verify, then `read_file`) is not reinforced strongly enough in the late-stage decision boundary where budgets are low.
  - Finalization heuristic allows `STOP` immediately after first successful `bash`, with insufficient "must-do-last" guidance.

## Work plan (target: `tool_use_correctness=pass` for 3/3)

1. Prompt hardening for shell syntax safety:
   - Update deep-eval instruction blocks (global ReAct policy + `sandbox_build_verify` prompt) with strict rule:
     "For `bash`, use exactly one command per call; never use `;`, `&&`, `||`, pipes, or multiline chains."
   - Add allowed examples (`python -m pytest test_greet.py`) and disallowed examples (`cd x; pytest`).

2. Denial-aware recovery guidance:
   - Add explicit retry policy in prompt text:
     "If a tool call is denied, immediately retry with corrected syntax; do not repeat denied pattern."
   - Keep this scoped to syntax/tool-handler denials to avoid over-constraining general behavior.

3. Terminal-step contract reinforcement for `sandbox_build_verify`:
   - Strengthen scenario wording so required closure is explicit and ordered:
     `file_write -> bash -> file_edit (if needed) -> bash -> read_file -> final answer`.
   - Add "no final answer before `read_file`" clause.

4. Eval harness guardrail (optional if prompt-only fix is insufficient):
   - Add per-scenario metadata hint for shell-safe mode in eval prompt assembly.
   - If repeated bash-denied pattern persists, introduce a lightweight runtime nudge message after the second identical denial.

5. Verification protocol:
   - Run targeted tests for prompt/template composition and eval scenario integrity.
   - Re-run `uv run agent-driver eval run --suite deep`.
   - Accept when `tool_use_correctness` is `3 pass, 0 partial, 0 fail` and `sandbox_build_verify` has no denied `bash` calls.

## Suite layout update (next iteration)

- `deep` suite becomes slim and fast:
  - `sandbox_build_verify`
  - `file_edit_minimal_patch` (new)
- Stable scenarios move to new `regression` suite:
  - `repo_audit_report`
  - `web_to_repo_migration_plan`
- `all` suite now means `default + deep + regression`.

## How to validate without full live reruns

- Prefer targeted offline tests first:
  - `tests/tools/test_builtin_shell_tools.py`
  - `tests/prompts/test_react_base_policy_shell_rules.py`
  - `tests/runtime/test_denial_recovery_hint.py`
  - `tests/runtime/test_final_answer_strips_text_form_tool_calls.py`
  - `tests/runtime/test_text_form_calls_continue_loop.py`
  - `tests/runtime/test_real_scenarios.py`
  - `tests/cli/test_eval_answer_scoring.py`
  - `tests/cli/test_eval_suite_membership.py`
  - `tests/cli/test_eval_cli.py`
- Run live only when offline checks are green:
  - `uv run agent-driver eval run --suite deep`
  - `uv run agent-driver eval run --suite regression` (periodic confidence run)

## Post-plan validation (latest)

- Latest live `deep` bundle: `.agent-driver/evals/20260519-152514`
  - scenarios: 2 (`sandbox_build_verify`, `file_edit_minimal_patch`)
  - `tool_use_correctness`: 2 pass, 0 partial, 0 fail
  - `sandbox_build_verify`: no denied `bash` calls, required `read_file` closure satisfied
- Live `regression` bundle (periodic): `.agent-driver/evals/20260519-151533`
  - scenarios: 2 (`repo_audit_report`, `web_to_repo_migration_plan`)
  - runtime status: both completed (no terminal failures)
- Targeted offline verification:
  - `uv run pytest tests/tools/test_builtin_shell_tools.py tests/prompts/test_react_base_policy_shell_rules.py tests/runtime/test_denial_recovery_hint.py tests/cli/test_eval_suite_membership.py tests/cli/test_eval_cli.py`
  - result: all green

## Functional scenarios (this iteration)

- `bash_denial_recovery` — проверка recovery после `tool_handler_error` в `bash`.
- `loop_detection_force_final` — проверка остановки без лупа после пустого `grep_search`.
- `workspace_cwd_relative_paths` — запись/чтение файлов только по относительным путям в sandbox.
- `web_zero_results_honest_finalize` — честное завершение при пустом `web_search`.
- `todo_status_lifecycle` — корректный lifecycle статусов `todo_write`.
- `multi_file_rename` — координированный `file_edit` по двум файлам.
- `python_sandbox_arithmetic` — безопасный вызов `python` tool в `python_exec`.
- `forbidden_bash_governance` — соблюдение `forbidden_tools=(bash,)` под провоцирующим запросом.
- `multi_file_summary_digest` — многофайловое чтение и структурный digest.

## Coverage matrix

- `bash_denial_recovery` -> `tool_stage` denial-recovery path + shell policy adaptation.
- `loop_detection_force_final` -> bounded search behavior and non-loop finalize.
- `workspace_cwd_relative_paths` -> `workspace_cwd` relative-path resolution for fs tools.
- `web_zero_results_honest_finalize` -> web zero-result terminal behavior.
- `todo_status_lifecycle` -> strict `todo_write` schema/status transition rules.
- `multi_file_rename` -> multi-file `file_write` + `file_edit` consistency.
- `python_sandbox_arithmetic` -> python tool execution path (`python_exec` pack).
- `forbidden_bash_governance` -> forbidden tool governance under temptation.
- `multi_file_summary_digest` -> long-context multi-read synthesis quality.
- `chat_multi_turn_followup` -> multi-turn session continuity (`follow_up_prompts`).
- `ambiguous_request_clarify_then_act` -> clarification then execution.
- `real_refactor_small_module` -> dogfood refactor in sandbox.

## Engine fixes (2026-05-20)

### Bug: text-form `<tool_call>` leaked into final answer

- Symptom: models without native tool_calls (Qwen/Llama style) emit
  `<tool_call>{...}</tool_call>` in assistant text; this markup appeared in
  `AgentRunOutput.answer` (e.g. `multi_file_rename` live run).
- Fix: `SingleAgentOutputMixin._sanitize_terminal_answer()` applies
  `strip_text_form_tool_calls()` before persisting terminal answer; raw content
  kept in `metadata.raw_assistant_content`.
- Regression: `tests/runtime/test_final_answer_strips_text_form_tool_calls.py`

### Bug: text-form tool calls did not continue ReAct loop

- Symptom: when `finish_reason=STOP` but content contains text-form tool calls,
  runner finalized instead of executing tools and calling LLM again.
- Fix: `_finalize_tool_stage_transition` treats planned calls with
  `metadata.text_form_source` like native `TOOL_CALLS` for loop continuation.
- Regression: `tests/runtime/test_text_form_calls_continue_loop.py`,
  `tests/runtime/test_real_scenarios.py::test_text_form_tool_call_recovery_*`

### Scoring: language-agnostic answer assertions

- Added `EvalScenario.expected_answer_any_of` (AND of OR-groups).
- Applied to `loop_detection_force_final`, `web_zero_results_honest_finalize`.
- Tests: `tests/cli/test_eval_answer_scoring.py`

## Real scenarios (deep suite extension)

- `chat_multi_turn_followup` — two turns with shared `thread_id` and follow-up prompt.
- `ambiguous_request_clarify_then_act` — clarify-then-act with `expected_answer_any_of`.
- `real_refactor_small_module` — sandbox docstring refactor (`read_file` / `file_edit`).

Offline-only regressions (not in live suite):

- `tests/runtime/test_real_scenarios.py` — text-form recovery, interrupt resume approve,
  session digest persistence across runs.

## Functional pass validation

- Offline smoke bundle: `.agent-driver/evals/20260520-071357`
  - suite: `deep`
  - scenarios: 11
  - terminal failures: 0
- Live bundle: `.agent-driver/evals/20260520-071401`
  - suite: `deep`
  - scenarios: 11
  - terminal failures: 0
  - `tool_use_correctness`: 11 pass, 0 partial, 0 fail
  - `sandbox_build_verify`: no denied `bash` calls
