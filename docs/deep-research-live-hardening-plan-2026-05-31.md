# Deep Research Live Hardening Plan

Status: active execution plan, created 2026-05-31.

This plan is for the failure class observed in live chat-demo runs: the model
starts a visible plan, writes a long report-like answer in chat while the plan
is still `0/N`, the runtime asks for repair, and the model rewrites the same
large answer token by token. The goal is not to add more prompts and hope. The
goal is an artifact-first Deep Research runtime that is measurable in Phoenix,
Playwright screenshots, backend logs, and per-run trace summaries.

## Executive Decision

Use a three-profile Deep Research architecture and compare them live:

- `light`: single agent, web search plus web fetch only, short answer, no report
  artifact, no subagents.
- `medium`: parent orchestrator plus bounded subagents, durable
  `research/report.md`, source ledger, targeted file edits, and concise final
  chat answer.
- `hard`: medium plus verifier/auditor workers, claim-source matrix, broader
  source budget, optional computation, and PDF export from the report artifact.

The recommended product default is `medium`. `light` is a fast lookup mode, not
Deep Research. `hard` is opt-in for broad, ambiguous, high-value questions where
the token cost earns its keep.

## Current Truth In This Codebase

These facts come from local code inspection on 2026-05-31.

- `deep_research` preset currently enables `web`, `planning_progress`,
  `filesystem_read`, `filesystem_write`, `artifacts`, `skill_tool`, and
  `skill_view` in
  `examples/chat-demo/backend/app/services/agent_factory.py`.
- `filesystem_read` provides `read_file`, `glob_search`, and `grep_search`.
- `filesystem_write` provides `file_write`, `file_edit`, `file_patch`, and
  `notebook_edit`.
- `artifacts` provides `artifact_list`, `artifact_read`, and
  `artifact_preview`.
- The same factory sets
  `SubagentSettings(enable_subagents=effective_preset != "deep_research")`.
  This means Deep Research has no working subagents even though product
  behavior and screenshots expect parallel research.
- `deep-research-report/SKILL.md` says `allowed_tools: [web_search, web_fetch,
  todo_write, run_subagent]`. That is stale: the real tool name is
  `agent_tool`, and the skill does not mention `file_write`, `read_file`,
  `file_edit`, `file_patch`, or artifact tools.
- The chat-demo workspace API currently has artifact list and preview endpoints
  only. There is no raw download endpoint and no Markdown-to-PDF export.
- `examples/chat-demo/frontend/tests/e2e/chat_live_probe.py` is already a real
  Playwright live probe. It sends messages through the UI, captures run IDs,
  fetches `/api/chat/runs/{run_id}/trace-summary`, checks workspace artifacts,
  saves screenshots, and writes scorecards under `/tmp/chat-demo-live`.

## Why The Current Behavior Failed

The visible bug is not just "bad prompt". It is a mismatch among tool surface,
runtime contract, and UI:

- The model can write a long draft in chat before it has written
  `research/report.md`.
- The final-readiness contract can detect incomplete todos, but repair nudges
  still allow another long assistant message instead of a mandatory tool call.
- The Deep Research skill says to delegate but names a tool that does not exist.
- The Deep Research preset disables subagents, so a model trying to delegate
  must either fail or choose a different strategy.
- The UI can preview artifacts but cannot download the final artifact as raw
  Markdown or PDF.
- Search candidates are visually rich in the chat, but fetched/verified source
  coverage is still too weak in the failure traces.

The architecture must make the healthy path cheaper than the unhealthy path:
write report content to disk, patch it, preview it, and summarize it. Do not
make the model carry a 13-point draft in the transcript.

## Initial Live Smoke

After adding the matrix harness, one `light` live run was executed against
`frames-003`:

- run id: `run_ee209de23958`
- artifact dir:
  `/tmp/chat-demo-live-deep-research-plan-smoke/deep-light-frames-003`
- result: expected answer was found.
- cost signal: 30,240 total tokens for a short-answer task.
- tool chain:
  `web_search -> web_search -> web_fetch -> web_search -> web_search -> web_search -> web_search`
- source signal: one fetched domain only.
- trace signal: backend summary reported `research.required=False` even though
  the model used web tools.

This does not block the plan, but it validates the problem statement: even the
light path can be wasteful without explicit search/fetch budgets and trace
scorecards.

## External Practices To Adopt

OpenAI Deep Research is explicitly a long-running tool-using task. The API
guide recommends background mode for long work and uses `max_tool_calls` as the
primary cost and latency control. It also treats web search, file search, MCP,
and code interpreter as data sources/tools for a research run:
https://platform.openai.com/docs/guides/deep-research

OpenAI Agents SDK tracing records agent runs, LLM generations, function tool
calls, handoffs, guardrails, and custom events. Our Phoenix trace and local
trace-summary should have the same debugging shape:
https://openai.github.io/openai-agents-python/tracing/

OpenAI Agents SDK guardrails distinguish workflow-level guardrails from
tool-level guardrails. Deep Research needs tool-level guardrails around
phase/order, artifact writes, repeated searches, and final answer length:
https://openai.github.io/openai-agents-js/guides/guardrails

Anthropic's multi-agent research writeup uses a lead agent that plans and
launches parallel subagents for independent search paths. It also warns by
example: multi-agent systems buy breadth and coverage with much higher token
usage, so they must be gated by task shape and measured:
https://www.anthropic.com/engineering/built-multi-agent-research-system

OpenAI BrowseComp is useful as a design signal: hard browsing requires
persistence, search reformulation, short verifiable answers, and the
"hard-to-find but easy-to-verify" pattern. Do not copy hidden or leak-sensitive
benchmark items into public docs; use the method and public examples only:
https://openai.com/index/browsecomp/

FRAMES is a public Apache-2.0 benchmark with 824 multi-hop questions requiring
2-15 Wikipedia articles. We use a small public sample as a live smoke matrix:
https://huggingface.co/datasets/google/frames-benchmark

GAIA is relevant for autonomous tool-use evaluation, but its dataset is gated
and explicitly asks users not to reshare validation/test content. Use it as a
benchmark family reference only unless a private eval environment is configured:
https://huggingface.co/datasets/gaia-benchmark/GAIA

## Best From Neighbor Projects

### OpenClaude

Useful patterns from `/home/roman/pyprojects/ML/openclaude`:

- Separate `Read`, `Write`, `Edit`, `Glob`, and `Grep` tools instead of sending
  routine file work through shell.
- `Write` requires reading existing files first and says it is for new files or
  full rewrites.
- `Edit` requires reading first and encourages the smallest unique replacement.
- `AgentTool` tells the parent to launch parallel agents in a single message
  when work is independent.
- `AgentTool` warns not to read child transcripts just to peek, because that
  pulls tool noise into parent context.
- Large tool results are persisted to session-local `tool-results` files with
  a preview and a path.
- The agent list can be injected as a message rather than embedded in the tool
  schema, which avoids cache churn from dynamic tool descriptions.

Adopt:

- Read-before-edit/write for `research/report.md`.
- Prefer `file_patch`/`file_edit` after initial `file_write`.
- Keep subagent outputs summarized; parent synthesizes.
- Persist large tool outputs and expose only previews in context.

### Hermes Agent

Useful patterns from `/home/roman/pyprojects/ML/hermes-agent`:

- `delegate_task` creates isolated child contexts, restricted toolsets, own
  task IDs, batch parallel mode, max concurrency, child timeouts, and depth
  limits.
- Parent receives summaries, not full child transcripts.
- `file_tools.py` deduplicates repeated reads and eventually blocks unchanged
  re-read loops.
- File writes/patches use per-path locks and stale-read warnings.
- `tool_result_storage.py` has three layers: per-tool cap, spill oversized
  results to sandbox files, and enforce aggregate per-turn output budget.
- Subagents cannot recursively delegate unless explicitly granted an
  orchestrator role.

Adopt:

- Bounded fan-out: default 2-3 children for medium, 3-5 for hard.
- Flat subagents by default.
- Child toolsets are strict subsets of parent toolsets.
- Parent owns `research/report.md`; children write notes only or return compact
  summaries.
- Add stale artifact edit detection and repeated-read/search loop guards.

### Skill.md Discipline

The current skill mechanism is useful, but the `deep-research-report` skill must
become executable:

- Metadata must list real tools only.
- The skill should be short enough to load often, with detailed rubrics in
  referenced files only when needed.
- The workflow must say exactly where durable output belongs:
  `research/report.md`, `research/sources.jsonl`, and optional
  `research/claims.md`.
- The skill should define stop conditions and tool order, not only "be
  thorough".

## Architecture Profiles

### Profile 1: Light

Purpose:

- Fast source-backed answer.
- No durable report.
- No subagents.
- No filesystem writes.

Allowed tools:

- `todo_write` only when the user asks for a plan.
- `web_search`.
- `web_fetch`.
- `skill_tool` and `skill_view` only if the model needs a workflow hint.

Forbidden tools:

- `agent_tool`.
- `file_write`, `file_edit`, `file_patch`.
- `bash`, `python`.

Entry criteria:

- User asks for one short answer, one to three sources, a quick comparison, or a
  source-backed clarification.
- Expected output is under roughly 800 words.

Acceptance metrics:

- `web_fetch` count >= 1 for source-backed claims.
- Distinct fetched domains >= 1.
- No `agent_tool`.
- No report artifact required.
- Final answer includes visible links.
- Output tokens normally <= 4k.

Failure examples:

- Treating `web_search` snippets as verified evidence.
- Launching subagents for a simple answer.
- Creating `research/report.md` for a quick lookup.

### Profile 2: Medium

Purpose:

- Default Deep Research.
- Produce a durable Markdown report while keeping chat concise.
- Use bounded subagents for independent source discovery or narrow subtopics.

Allowed tools:

- `todo_write`.
- `web_search`, `web_fetch`.
- `read_file`, `glob_search`, `grep_search`.
- `file_write`, `file_edit`, `file_patch`.
- `artifact_list`, `artifact_read`, `artifact_preview`.
- `skill_tool`, `skill_view`.
- `agent_tool` with max 2-3 children.

Subagent policy:

- Parent creates and owns `research/report.md`.
- Children may search/fetch/read and return compact source notes.
- Children should not write the parent report.
- Parent launches children in one tool-call batch when questions are
  independent.
- Parent must synthesize child outputs and cite fetched URLs itself.

Entry criteria:

- User asks for a report, survey, comparison, architecture, or investigation.
- Requires multiple sources or several subtopics.
- Expected output can exceed 800 words.

Acceptance metrics:

- First material action is `todo_write` or a runtime-created plan event.
- `research/report.md` is created before any assistant chat prose over 1,500
  chars.
- `research/sources.jsonl` exists and has records.
- `agent_tool` used at least once when the task has independent subtopics.
- `web_fetch` successful count >= 3.
- Distinct fetched domains >= 2.
- `file_write` count for `research/report.md` <= 1 after initial creation.
- Subsequent report changes use `file_patch` or `file_edit` after a fresh
  `read_file`/`artifact_preview`.
- Final visible todos are complete or explicitly closed by artifact state.
- Final chat answer <= 1,200 chars and references `research/report.md`.
- No `deep_research_full_report_rewrite`, no `long_final_after_report`, no
  repeated identical search-query loops.

Failure examples:

- Report text appears in chat while plan remains `0/N`.
- `agent_tool` is unavailable or denied.
- `research/report.md` missing from workspace API.
- Parent leaves child outputs unsynthesized.

### Profile 3: Hard

Purpose:

- High-cost, high-coverage research with explicit verification.
- Useful for broad questions, ambiguous evidence, large comparisons, or
  report-to-PDF deliverables.

Allowed tools:

- Everything in `medium`.
- Optional `python` only when computation, table extraction, or chart/data
  processing is part of the task.
- Optional browser/PDF readers if later added.

Subagent policy:

- 3-5 children in waves.
- Wave 1: source discovery by independent angles.
- Wave 2: verification/citation auditor.
- Optional wave 3: contradiction/red-team reviewer.
- Parent still owns final report and final user response.

Required artifacts:

- `research/report.md`.
- `research/sources.jsonl`.
- `research/claims.md` or `research/claims.jsonl`.
- `research/checks.md`.
- Exported PDF when the user asks for downloadable deliverable.

Entry criteria:

- User asks for a comprehensive report, "deep research", literature review,
  due diligence, or decision memo.
- Need >= 8 verified sources or explicit citation audit.
- The task is worth a 2x-4x token budget over medium.

Acceptance metrics:

- `web_fetch` successful count >= 6.
- Distinct fetched domains >= 3.
- At least one verifier/auditor subagent.
- Claim-source matrix exists.
- PDF export endpoint returns a file when requested.
- Total token budget stays within configured hard limit.
- Phoenix trace shows parent, children, tool calls, artifact writes, and final
  readiness spans.

Failure examples:

- Hard mode is selected for quick lookup.
- Multiple children duplicate the same search query.
- Verifier never reads the report or sources.
- PDF export is simulated in text instead of returning a file.

## Runtime Design

### Deep Research Controller

Add a small runtime controller instead of relying only on prompt text:

1. Classify research profile: `light`, `medium`, `hard`.
2. Attach profile metadata to `task_contract`.
3. Create session workspace before the run starts.
4. For `medium` and `hard`, create or reserve:
   - `research/report.md`
   - `research/sources.jsonl`
   - optional `research/claims.md`
5. Apply a phase gate:
   - `plan`: `todo_write`
   - `discover`: `skill_tool`, `skill_view`, `web_search`, `agent_tool`
   - `verify`: `web_fetch`, `read_file`, child joins
   - `write`: `file_write`, `file_patch`, `file_edit`, `read_file`
   - `review`: `artifact_preview`, `artifact_read`, `todo_write`,
     verifier/auditor
   - `final`: concise chat response only
6. Enforce a long-output guard:
   - before first report write: block/redirect long chat prose to
     `research/report.md`;
   - after report exists: long final answer is a failure.
7. Run final-readiness over:
   - todo state;
   - fetched source coverage;
   - source ledger;
   - report existence and freshness;
   - final answer length and report reference.

### Tool Guardrails

Required guardrails:

- `web_search` duplicate query guard: repeated exact queries count as entropy
  failure unless a fetch or ledger update happened between them.
- `web_fetch` coverage guard: search-only synthesis is not final evidence.
- `file_write` report guard: initial create is allowed; full rewrites after
  report exists require explicit controller reason.
- `file_edit/file_patch` stale-read guard: edit requires a fresh report read or
  preview after last report write.
- `agent_tool` scope guard: child prompts must be bounded, name subtopic,
  expected source count, language, and no final synthesis authority.
- final answer guard: medium/hard final answer cannot duplicate the report.

### Frontend Contract

Artifact panel must support:

- List artifacts.
- Preview Markdown.
- Refresh while run is active.
- Download raw Markdown.
- Download PDF for `research/report.md`.
- Show last modified and size.
- Show source count and verified/blocked/candidate counts.

PDF export should be server-side:

- Endpoint: `GET /api/workspace/{session_id}/artifacts/{path}/download`.
- Endpoint: `GET /api/workspace/{session_id}/artifacts/{path}/pdf`.
- PDF conversion should be deterministic and log conversion errors.
- Frontend button is disabled until `research/report.md` exists.

## Implementation Phases

### Phase 0: Baseline Reproduction And Instrumentation

Goal: make the current failure impossible to hand-wave.

Checklist:

- [ ] Run the current fork-join prompt in the chat demo with live model.
- [ ] Save Playwright full-page screenshot.
- [ ] Save `/api/chat/runs/{run_id}/trace-summary`.
- [ ] Save Phoenix trace link or exported trace data.
- [ ] Save backend logs around run start/end and tool calls.
- [ ] Confirm whether `deep_research_artifact_expected` is true.
- [ ] Confirm tool chain contains or lacks `file_write`, `read_file`,
  `file_patch`, `agent_tool`.
- [ ] Confirm visible plan completion state at final.
- [ ] Record token usage, cost, search/fetch counts, artifact paths.

Gate:

- Do not change architecture until one current failure and one current pass are
  captured with screenshots and scorecards.

### Phase 1: Capability Surface Fix

Goal: make the tool surface honest.

Checklist:

- [ ] Split presets or profile metadata:
  - `research_light`
  - `deep_research_medium`
  - `deep_research_hard`
- [ ] Enable subagents for medium/hard Deep Research.
- [ ] Include `agent_tool` in medium/hard tool surface.
- [ ] Keep `bash` off for medium; keep `python` off unless hard allows it.
- [ ] Update `deep-research-report/SKILL.md` to list real tools:
  `web_search`, `web_fetch`, `todo_write`, `agent_tool`, `read_file`,
  `file_write`, `file_edit`, `file_patch`, `artifact_preview`,
  `artifact_read`, `artifact_list`.
- [ ] Add profile-specific skill sections:
  - light: source-backed short answer;
  - medium: artifact-first report plus bounded subagents;
  - hard: verifier/auditor and export.
- [ ] Add a prompt/contract check that rejects non-existent tool names in skill
  metadata during startup or tests.

Live check:

```bash
CHAT_DEMO_URL=http://localhost:5174 \
  .venv/bin/python examples/chat-demo/frontend/tests/e2e/chat_live_probe.py \
  --scenario subagent-synthesis \
  --scenario deep-research-artifact
```

Gate:

- `deep-research-artifact` must either use `agent_tool` when required or fail
  with a clear capability-surface failure, not silently proceed as 0/5 prose.

### Phase 2: Artifact-First Controller

Goal: make long report text go to files before chat.

Checklist:

- [ ] Add controller state to `ResearchSessionContract` or adjacent
  `DeepResearchControllerState`.
- [ ] For medium/hard, ensure `research/report.md` is created before long
  synthesis.
- [ ] Tighten `maybe_capture_deep_research_draft`: lower threshold for
  medium/hard to 1,500-2,000 chars and emit a tool/metadata event.
- [ ] Add a hard nudge when report exists and todos are stale: allowed next
  tool is `todo_write`, `artifact_preview`, `read_file`, `file_patch`, or
  `file_edit`, not another long final answer.
- [ ] Add a trace field:
  `first_report_update_before_long_chat: bool`.
- [ ] Add a trace field:
  `long_chat_before_report_chars: int`.
- [ ] Add a trace field:
  `report_full_write_count` and `report_patch_count`.

Live check:

```bash
CHAT_DEMO_URL=http://localhost:5174 \
  .venv/bin/python scripts/deep_research_live_matrix.py \
  --profiles medium \
  --question-id fork-join-canary \
  --limit 1
```

Gate:

- No assistant message over 1,500 chars before `research/report.md` exists.
- If final readiness fails, the next step patches/todos/artifact state instead
  of rewriting the report in chat.

### Phase 3: Medium Subagent Orchestration

Goal: use subagents for breadth without turning the run into entropy soup.

Checklist:

- [ ] Add medium planner rule: spawn 2-3 children only if the task has
  independent subtopics or source families.
- [ ] Child prompts must include:
  - exact subtopic;
  - max searches;
  - max fetched URLs;
  - output schema;
  - language;
  - "do not write final report".
- [ ] Parent must write child findings into source ledger or notes.
- [ ] Parent must synthesize into `research/report.md`.
- [ ] Add trace fields:
  - child_count;
  - child_tool_names;
  - child_fetch_count;
  - child_summary_chars;
  - duplicated_child_queries.
- [ ] Add scorecard checks for child duplication and parent synthesis.

Live check:

```bash
CHAT_DEMO_URL=http://localhost:5174 \
  .venv/bin/python scripts/deep_research_live_matrix.py \
  --profiles medium \
  --limit 3
```

Gate:

- At least one medium run uses subagents and still creates a report.
- Parent final answer references the report and does not paste child summaries
  verbatim.

### Phase 4: Hard Mode, Citation Audit, And Export

Goal: create a high-confidence mode that justifies extra token spend.

Checklist:

- [ ] Add hard profile metadata and UI selector if needed.
- [ ] Add verifier/auditor subagent role.
- [ ] Add `research/claims.md` or `research/claims.jsonl`.
- [ ] Add source quality rubric:
  - verified;
  - blocked;
  - candidate only;
  - contradicted;
  - stale/date-sensitive.
- [ ] Add claim-source matrix generation.
- [ ] Add raw Markdown download endpoint.
- [ ] Add server-side PDF export endpoint.
- [ ] Add frontend buttons for Markdown and PDF download.
- [ ] Add Playwright assertions that buttons appear only when report exists.

Live check:

```bash
CHAT_DEMO_URL=http://localhost:5174 \
  .venv/bin/python scripts/deep_research_live_matrix.py \
  --profiles hard \
  --limit 2
```

Gate:

- Hard run creates report, sources, claims/checks artifact, and downloadable PDF
  when requested.

### Phase 5: Benchmark Matrix And Regression Policy

Goal: compare the three approaches and keep them from regressing.

Checklist:

- [ ] Use `examples/chat-demo/frontend/tests/e2e/deep_research_benchmark_questions.json`.
- [ ] Run light/medium/hard against the fork-join canary and public FRAMES
  sample.
- [ ] For every run, save:
  - screenshot;
  - trace-summary;
  - scorecard;
  - workspace artifacts;
  - Phoenix trace reference;
  - backend log excerpt if failed.
- [ ] Generate matrix scorecard:
  - tokens;
  - wall-clock;
  - tool chain;
  - search/fetch/domain counts;
  - child count;
  - report update pattern;
  - answer correctness;
  - failure flags.
- [ ] Compare profiles on the same questions.

PR gate:

- 2 light live canaries.
- 1 medium live Deep Research run.
- No fake-only pass is allowed for Deep Research behavior changes.

Nightly/manual gate:

- 10 benchmark questions x selected profiles.
- Use `--profiles light,medium` for cost-controlled nightly.
- Use `--profiles light,medium,hard` before release.

## Benchmark Seed Set

The public seed set lives in:

`examples/chat-demo/frontend/tests/e2e/deep_research_benchmark_questions.json`

It contains:

- `fork-join-canary`: our product-specific regression case.
- 10 public FRAMES questions with expected answers and source URLs.

Do not paste gated GAIA validation/test questions or hidden BrowseComp items
into docs, prompts, or public artifacts.

## Live Scripts

### Run Matrix

```bash
CHAT_DEMO_URL=http://localhost:5174 \
CHAT_DEMO_LIVE_ARTIFACT_DIR=/tmp/chat-demo-live-deep-research \
  .venv/bin/python scripts/deep_research_live_matrix.py \
  --profiles light,medium \
  --limit 3
```

Use `--dry-run` to print the matrix without spending model tokens.

### Audit Trace Artifacts

```bash
.venv/bin/python scripts/deep_research_trace_audit.py \
  /tmp/chat-demo-live-deep-research \
  --format md \
  --fail-on-risk
```

The audit is intentionally trace/artifact-based. It does not mock SSE and does
not grade a canned transcript.

## Scorecard Criteria

Each live run should produce a row with these fields:

- scenario id;
- profile;
- run id;
- final verdict;
- terminal event;
- tool chain;
- first tool;
- prompt tokens;
- completion tokens;
- total tokens;
- output tokens after first report update;
- search count;
- fetch count;
- fetch attempts;
- unique domains;
- subagent child count;
- report artifact present;
- source ledger present;
- report full writes;
- report patches/edits;
- stale edits;
- repeated report reads;
- repeated search queries;
- phase violations;
- long final after report;
- visible plan completion;
- expected answer found;
- screenshot path;
- Phoenix enabled/configured status.

## Failure Flags That Block Progress

Any of these should block a phase unless explicitly accepted as known risk:

- `missing_terminal_event`
- `run_failed_or_cancelled`
- `search_only_research_report`
- `plan_todos_incomplete_on_final`
- `deep_research_no_report_artifact`
- `deep_research_no_source_ledger_artifact`
- `deep_research_full_report_rewrite`
- `deep_research_stale_report_edit`
- `deep_research_repeated_report_read`
- `deep_research_final_missing_report_reference`
- `deep_research_missing_initial_todo`
- `deep_research_skill_denied`
- `deep_research_low_verified_coverage`
- `deep_research_preliminary_final`
- `deep_research_repeated_search_args`
- `deep_research_search_without_fetch_progress`
- `deep_research_tool_entropy_high`
- `deep_research_phase_violation`
- `deep_research_long_final_after_report`
- `required tool missing: agent_tool` for medium/hard
- no expected answer in final transcript or report preview

## Periodic Checks

After every runtime/prompt/tool change:

- Run one light canary.
- Run one medium fork-join canary.
- Inspect screenshot and scorecard.
- Open Phoenix trace and confirm spans are coherent.

At the end of every phase:

- Run the matrix with at least three public benchmark questions.
- Write a short dated note in docs with:
  - git commit;
  - model/provider;
  - run IDs;
  - scorecard summary;
  - failures and next action.

Before release:

- Run 10 benchmark questions across light and medium.
- Run at least two hard questions.
- Confirm Markdown and PDF downloads from the UI.

## Definition Of Done

Deep Research is not considered fixed until all are true:

- Medium Deep Research can create and patch `research/report.md`.
- Medium and hard can use subagents.
- The parent, not children, owns final synthesis.
- The UI shows artifacts while the run is active.
- The user can download Markdown and PDF when a report exists.
- Phoenix and trace-summary show token/tool/source metrics.
- Live Playwright screenshots show no 0/N plan with long answer prose above it.
- The fork-join canary passes twice on live model/provider.
- At least 8 of 10 public benchmark seed questions answer correctly in the
  selected profile, or failures are documented as source/tool/provider issues.

## Immediate Next Work Order

1. Run `scripts/deep_research_live_matrix.py --dry-run` and confirm the matrix.
2. Run one `light` live question to validate script plumbing.
3. Fix Deep Research subagent availability and stale skill metadata.
4. Run one `medium` live question and expect it to fail only on missing
   artifact/controller gates, not tool availability.
5. Implement artifact-first controller gates.
6. Re-run medium fork-join canary until no long chat rewrite occurs.
7. Add download/PDF endpoints and frontend buttons.
8. Run hard profile with verifier/auditor and export checks.
