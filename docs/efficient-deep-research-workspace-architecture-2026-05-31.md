# Efficient Deep Research Workspace Architecture

Status: active proposal. Implementation order is tracked in
[Unified Work Plan](unified-work-plan-2026-05-31.md) phases 1-4.

This document fixes the bug class where Deep Research writes a large draft in
chat, the runtime notices unfinished todos, then the model is forced to produce
the same long answer again token by token. The target behavior is simple:
long-lived research output must be a session artifact on disk, not only an
assistant message in the transcript.

## Problem

In the fork-join queue test, the model created a visible plan and then wrote a
large partial article during step 2. The UI still had unfinished todos, so the
runtime treated the answer as not final and pushed the model back into the loop.
Because the draft existed only in chat text, the next LLM call had no cheap,
authoritative place to resume from. The model rewrote the article instead of
patching the existing draft.

The immediate architectural smell:

- chat text is doing three jobs at once: user-visible progress, scratchpad, and
  final deliverable;
- todo state is separate from the produced draft;
- Deep Research has no required write-through artifact contract;
- the frontend can show source ledgers, but not the evolving report file;
- filesystem write tools exist but are not enabled for the normal research
  preset.

## Current Local Baseline

Relevant pieces already exist:

- `examples/chat-demo/backend/app/workspace.py` creates a per-session workspace
  and passes `workspace_cwd` through `app_metadata`.
- `ToolSet.packs("filesystem_read")` exposes `read_file`, `glob_search`,
  `grep_search`; `filesystem_write` exposes `file_write`, `file_edit`,
  `notebook_edit`.
- The `workspace` preset currently enables `web`, `planning_progress`, and
  `filesystem_read`, but not `filesystem_write`.
- `ResearchSessionContract` tracks research evidence, source ledger, source
  links, and unfinished todos.
- `DeepResearchPanel` shows source/citation diagnostics, but no artifact tree or
  report preview.

This means the minimum viable fix is not "invent files"; it is "make files the
first-class output surface for research".

## External Reference Points

OpenAI Deep Research frames long research as a tool-using, long-running task
over web search, file search, remote MCP, and code interpreter, and recommends
background mode for work that can take tens of minutes. It also supports
`max_tool_calls` as the main cost/latency control.
Source: https://developers.openai.com/api/docs/guides/deep-research

OpenAI Agents SDK separates hosted tools, local runtime tools, function tools,
agents-as-tools, and workspace-scoped Codex tasks. The important pattern for us
is that local/runtime tools are executed by the application environment, while
the model only decides when to call them.
Source: https://openai.github.io/openai-agents-python/tools/

Anthropic Managed Agents include a pragmatic filesystem/web/shell toolset:
`bash`, `read`, `write`, `edit`, `glob`, `grep`, `web_fetch`, `web_search`.
They also spill tool outputs over 100K tokens into sandbox files and give the
model a path plus preview.
Source: https://platform.claude.com/docs/en/managed-agents/tools

Claude Code documents the core failure mode: the context window contains
conversation history, file contents, command outputs, skills, and instructions;
when context fills, older tool outputs are cleared and conversation is
summarized. If large outputs refill context after compaction, it stops instead
of looping. That is exactly why durable artifacts and resumable reads are
needed for large research drafts.
Source: https://code.claude.com/docs/en/how-claude-code-works

MCP roots formalize the idea that clients expose bounded filesystem roots to
servers. Our per-session research workspace should behave like a root: tools
may operate inside it by default and must not escape without explicit policy.
Source: https://modelcontextprotocol.io/specification/2025-06-18/client/roots

## Local Comparisons

### OpenClaude

Observed useful patterns in `/home/roman/pyprojects/ML/openclaude`:

- Read/write/edit are separate tools. Write explicitly says it overwrites and
  should be used for new files or full rewrites; Edit is for targeted exact
  replacements.
- Grep prompt says to use the dedicated Grep tool instead of `grep`/`rg` through
  Bash, because permissions and access are modeled there.
- Glob is optimized for large codebases and returns paths.
- Tool history compression keeps recent tool results full, truncates mid-tier
  results, and replaces old bulk with explicit stubs that say how many chars
  were omitted.
- Permissions distinguish read-only tools from shell and file modification.

Takeaway: native read/search/edit tools should be preferred over bash for
routine research artifact work, and old tool output compression must be
explicit rather than silent.

### Hermes Agent

Observed useful patterns in `/home/roman/pyprojects/ML/hermes-agent`:

- Core tools include `read_file`, `write_file`, `patch`, `search_files`,
  `terminal`, `todo`, `session_search`, `execute_code`, and `delegate_task`.
- The `file` toolset groups read/write/patch/search as one capability.
- `tool_result_storage.py` persists oversized tool results into sandbox files
  and returns a preview plus `read_file` instructions.
- `file_tools.py` has read deduplication: repeated reads of the same unchanged
  region return a stub, then eventually block the loop.
- `patch` supports exact replacement and V4A patch format, with path and stale
  write guards.

Takeaway: for long research, a patch/diff tool is more token-efficient than
full-file writes, and large outputs should spill to files with readable paths.

## Target Architecture

### 1. Session Workspace As Product Primitive

Every chat session must have a server-side workspace:

```text
workspace/
  <session_id>/
    research/
      report.md
      notes.md
      sources.jsonl
      outline.md
      checks.md
    tool-results/
      <tool_call_id>.txt
    uploads/
    .agent-driver/
      manifest.json
      artifact-index.json
```

Rules:

- `workspace_cwd` is created when the session is created, not lazily only when
  tools are used.
- Deep Research creates `research/report.md` before the first long synthesis.
- The assistant is instructed to update this file incrementally and keep chat
  messages short.
- All file paths exposed to tools are relative to the session root unless a
  trusted admin preset allows wider roots.
- The runtime records artifact metadata in session/run metadata and streams
  artifact events to the frontend.

### 2. Research Artifact Contract

Add a `ResearchArtifactContract` beside `ResearchSessionContract`:

```python
ResearchArtifactContract(
    enabled=True,
    workspace_root=...,
    report_path="research/report.md",
    source_ledger_path="research/sources.jsonl",
    outline_path="research/outline.md",
    required_before_long_answer=True,
    final_answer_mode="summarize_artifact_with_link",
)
```

The contract is satisfied when:

- `research/report.md` exists and has non-trivial content;
- the current todo state is either complete or the remaining open todos are
  explicitly marked as final-polish/source-check items;
- source ledger has verified reads when research depth requires it;
- final chat answer references the artifact path and gives a short summary,
  not a full duplicate of the artifact.

Critical behavior: if the model emits a long answer while Deep Research artifact
mode is enabled and no report artifact was updated, the runtime should store
that answer as `research/report.md` before any repair turn. This turns the
current bug into a recoverable write-through event.

### 3. Tool Surface For Deep Research

Deep Research should not get unrestricted dev tools by default. It should get a
research-safe filesystem surface:

Required:

- `glob_search`
- `grep_search`
- `read_file`
- `file_write`
- `file_edit`
- `web_search`
- `web_fetch`
- `todo_write`
- `agent_tool` for parallel source discovery
- `skill_tool` and `skill_view` for curated research skills

Add next:

- `file_patch`: structured patch/diff application, safer than bash redirection
  and cheaper than full overwrite.
- `artifact_list`: list session artifacts with path, size, mtime, kind.
- `artifact_read`: bounded read by path, offset, limit.
- `artifact_preview`: markdown/html preview metadata for UI.
- `source_ledger_write`: append normalized source/evidence records without
  forcing the model to rewrite JSON manually.

Keep gated/off by default:

- `bash` or broad shell tools;
- `python` unless the query needs computation/data analysis;
- `file_delete`;
- arbitrary absolute path writes.

### 4. Artifact-First Runtime Loop

Deep Research loop should be:

1. classify request as `deep_parallel_research`;
2. create workspace and artifact manifest;
3. force or strongly prefer initial `todo_write`;
4. create `research/outline.md` and `research/report.md`;
5. discover sources in parent or subagents;
6. write notes/source ledger incrementally;
7. update `report.md` section by section via `file_edit` or `file_patch`;
8. run final-readiness contract over artifact + todos + source ledger;
9. final chat answer is short: summary, caveats, source count, artifact link.

The runtime should treat a long assistant message as a signal:

- if length exceeds `deep_research_inline_answer_max_chars`, intercept and store
  it into `research/report.md`;
- append a tiny assistant-visible message: "Draft captured to
  research/report.md. Continue from that file; do not rewrite from scratch.";
- continue repair with `read_file(report.md, offset=-N)` or artifact summary,
  not with full transcript text.

### 5. Final-Readiness Rules

The current force-final path is too willing to ignore todos for research. Change
the policy:

- For normal chat deliverables, a final synthesis todo may be auto-covered by a
  meaningful final answer.
- For Deep Research artifact mode, unfinished todos do not require rewriting the
  answer if `report.md` exists. They require either a targeted artifact patch or
  a todo status update.
- If final readiness fails only because todos are stale, force `todo_write`, not
  a full final answer.
- If final readiness fails because source links are missing, ask the model to
  patch citations into `report.md`, not regenerate the whole report.
- If near budget and report artifact exists, final answer must summarize the
  artifact; if no artifact exists, write-through current draft first.

### 6. Frontend Projection

Add a workspace/artifacts panel to the chat UI:

- show `research/report.md`, `outline.md`, `sources.jsonl`, generated files;
- show size, last updated time, and producing tool/run;
- render `report.md` preview in a side panel;
- expose download/copy/open actions;
- show artifact update events inline near tool calls;
- keep `DeepResearchPanel` for source coverage, but add artifact status:
  `report updated`, `N sections`, `last patch`, `final ready`.

SSE/runtime events:

```json
{"event":"artifact_created","data":{"path":"research/report.md","kind":"report"}}
{"event":"artifact_updated","data":{"path":"research/report.md","bytes":12345,"operation":"patch"}}
{"event":"artifact_index_updated","data":{"count":4}}
{"event":"research_artifact_ready","data":{"report_path":"research/report.md"}}
```

Backend endpoints:

- `GET /api/workspace/{session_id}/files`
- `GET /api/workspace/{session_id}/files/{path}`
- `GET /api/workspace/{session_id}/artifacts`
- `GET /api/workspace/{session_id}/artifacts/{artifact_id}/preview`

All endpoints must path-normalize under the session root and deny traversal.

### 7. Context And Token Policy

Research artifacts change context policy:

- never put full `report.md` back into the prompt automatically;
- include only artifact index, outline, latest section headings, and tail
  excerpts;
- use `read_file` only for targeted sections;
- spill oversized tool outputs into `tool-results/<tool_call_id>.txt`;
- compress old tool results into explicit stubs with tool name, args, size, and
  path if available.

For prompts, add:

> In Deep Research mode, write durable research output to `research/report.md`.
> Chat messages should summarize progress. If you need to revise long text,
> read the relevant section and patch/edit the file. Do not rewrite the full
> report in chat.

### 8. Persistence Model

Session store should persist:

- `workspace_root`
- artifact index
- report path
- last artifact digest/hash
- source ledger path
- per-run artifact updates
- final artifact selected for answer

Runtime metadata should expose:

```json
{
  "deep_research_artifacts": {
    "workspace_root": "...",
    "report_path": "research/report.md",
    "report_exists": true,
    "report_size_bytes": 18234,
    "last_update_seq": 92
  }
}
```

### 9. Migration Plan

P0: stop the waste loop

- Add deep-research prompt rule: use `file_write`/`file_edit` for report drafts.
- Enable `filesystem_write` for `deep_parallel_research` only, scoped to session
  workspace.
- Add write-through guard for long assistant messages in Deep Research.
- If report artifact exists and todos are unfinished, force `todo_write` or
  artifact patch, not full answer regeneration.

P1: visible artifacts

- Add artifact index builder over the session workspace.
- Add backend list/read endpoints.
- Add frontend artifact panel and markdown preview.
- Emit `artifact_created`/`artifact_updated` events from filesystem write tools.

P2: efficient editing

- Add `file_patch` based on the Hermes/OpenAI apply-patch shape.
- Add stale-read guard: require `read_file` before editing existing report
  sections and warn when editing a file last read before another write.
- Add repeated-read dedupe for unchanged regions.

P3: research-specific storage

- Add `source_ledger_write` and durable `sources.jsonl`.
- Persist oversized web/tool outputs into `tool-results`.
- Add artifact-aware compaction and context projection.

P4: evals and regression suite

- Reproduce the fork-join deep research case.
- Assert no assistant message over threshold is emitted before a report artifact
  update.
- Assert no full-report rewrite after unfinished todo repair.
- Assert final chat answer links/summarizes `research/report.md`.

## Testing Strategy

The test suite should climb from deterministic contracts to live product traces.
The goal is not merely "the answer looks good"; the goal is to prove the agent
uses a low-entropy tool trajectory: planned search, bounded verification,
durable artifact writes, targeted edits, short final handoff.

### Layer 0: Pure Contracts

Fast unit tests, no provider:

- `ResearchArtifactContract`: report existence, size threshold, last update seq,
  final answer mode, and workspace path normalization.
- `ResearchSessionContract`: unfinished todo behavior when a report artifact
  exists versus when it does not.
- path safety: `research/report.md` allowed, `../escape.md` denied, absolute
  paths denied unless an admin preset explicitly allows them.
- long-answer threshold classifier: a long assistant message in Deep Research
  mode must be categorized as "capture to artifact before repair".
- artifact index builder: stable ordering, digest/hash, size, mtime, kind.

Expected command shape:

```bash
.venv/bin/python -m pytest \
  tests/runtime/test_research_session_contract.py \
  tests/context/test_artifact_store_conformance.py \
  tests/tools/test_builtin_filesystem_tools.py -q
```

New tests to add:

- `tests/runtime/test_research_artifact_contract.py`
- `tests/runtime/test_deep_research_write_through.py`
- `tests/tools/test_research_workspace_path_policy.py`

### Layer 1: Tool Unit And Governance Tests

These tests verify that Deep Research exposes exactly the right tools and no
ambient power:

- preset includes `web_search`, `web_fetch`, `todo_write`, `glob_search`,
  `grep_search`, `read_file`, `file_write`, `file_edit`, `agent_tool`,
  `skill_tool`, `skill_view`;
- preset does not include `bash` or `powershell_tool`;
- `python` is off by default and enabled only by a computation/data-analysis
  classifier;
- `file_write`/`file_edit` are allowed only under the session workspace;
- write/edit events emit artifact metadata.

Tool entropy checks:

- repeated identical `read_file` on unchanged regions is warned or deduped;
- repeated identical `web_search` with no new domains is flagged;
- repeated full `file_write` to `research/report.md` after initial creation is
  flagged; prefer `file_edit`/`file_patch`;
- `bash` for `grep`, `cat`, or redirection is forbidden in research preset.

### Layer 2: Deterministic Fake-Provider Runtime Scenarios

Use fake providers to replay exact bad and good trajectories:

1. **bad-old-loop reproduction**: model writes a 10K+ char report in assistant
   text while todos remain open. Runtime captures it to `research/report.md`,
   then asks for todo/artifact repair without re-streaming the report.
2. **good artifact-first run**: model calls `todo_write`, `web_search`,
   `web_fetch`, `file_write(report.md)`, `file_edit(report.md)`, `todo_write`,
   final short answer.
3. **missing source repair**: report exists, source coverage is insufficient;
   next forced step must be `web_fetch` or citation patch, not full answer.
4. **stale todo repair**: report exists and evidence is enough, but todo is
   stale; next forced step must be `todo_write`.
5. **path escape attempt**: model tries to write outside workspace; tool is
   denied and run continues with a safe path.

Assertions:

- artifact exists and contains the captured/generated report;
- final answer length is bounded;
- `tool_trace` contains expected subsequences;
- no second assistant message repeats more than a small similarity threshold of
  the captured report;
- metadata contains `deep_research_artifacts`.

### Layer 3: Deterministic Browser/Frontend Tests

Use chat-demo fake scenarios with Playwright:

- Deep Research button starts a run with research depth metadata.
- Workspace/artifact panel appears when `artifact_created` arrives.
- `research/report.md` preview updates after `artifact_updated`.
- Source ledger and artifact status coexist in `DeepResearchPanel`.
- Reload/replay reconstructs artifact metadata from the session.
- Mobile view keeps artifact panel reachable without breaking chat layout.

Expected command shape:

```bash
make test-chat-concepts CHAT_DEMO_URL=http://localhost:5174
.venv/bin/python examples/chat-demo/frontend/tests/e2e/chat_concepts_smoke.py \
  --scenario deep-research-artifact
```

### Layer 4: Trace Summary Evaluators

Extend `/api/chat/runs/{run_id}/trace-summary` and eval reports with a research
efficiency block:

```json
{
  "research_efficiency": {
    "tool_chain": ["todo_write", "web_search", "web_fetch", "file_write"],
    "tool_switches": 6,
    "repeated_tool_args": 0,
    "full_report_rewrites": 0,
    "artifact_updates": 3,
    "max_assistant_message_chars": 1800,
    "captured_long_answers": 1,
    "prompt_tokens": 13700,
    "completion_tokens": 4817,
    "tokens_per_verified_source": 925,
    "tokens_per_report_kb": 640,
    "estimated_wasted_tokens": 0
  }
}
```

Suggested failure flags:

- `deep_research_no_report_artifact`
- `deep_research_long_answer_not_captured`
- `deep_research_full_report_rewrite`
- `deep_research_no_artifact_update_before_final`
- `deep_research_bash_used_for_file_work`
- `deep_research_tool_entropy_high`
- `deep_research_repeated_search_args`
- `deep_research_tokens_over_budget`

Entropy heuristic:

- allow repeated `web_search` only when query/domain strategy changes;
- allow repeated `web_fetch` only for new URLs;
- allow one initial full `file_write(report.md)`, then prefer edits/patches;
- penalize long assistant messages after report artifact exists;
- penalize tool chains that bounce between search and final synthesis without
  artifact updates.

### Layer 5: Live Playwright + Phoenix Lanes

Live tests should be few, diverse, and expensive enough to be informative:

1. **short research**: one source-verified answer, expected
   `web_search -> web_fetch -> file_write -> final`.
2. **medium comparison**: compare 3-4 approaches, expected multiple domains and
   at least one artifact edit.
3. **long report with todos**: intentionally multi-section report; verifies no
   rewrite loop and final answer summarizes `research/report.md`.
4. **blocked fetch fallback**: one source fails/403s; verifies source ledger
   marks blocked reads and final caveat is explicit.
5. **parallel delegation**: subagents discover sources; parent synthesizes and
   writes final report artifact.
6. **computation add-on**: web research plus `python` only when numeric
   calculation is actually needed.

Run shape:

```bash
CHAT_DEMO_URL=http://localhost:5174 \
CHAT_DEMO_LIVE_ARTIFACT_DIR=/tmp/chat-demo-live-deep-research \
  .venv/bin/python examples/chat-demo/frontend/tests/e2e/chat_live_probe.py \
  --scenario deep-research-long-report \
  --scenario deep-research-blocked-fetch \
  --scenario deep-research-parallel
```

For each live run, inspect:

- browser screenshot: artifact panel visible, report preview readable;
- backend trace summary: required tools, forbidden tools, failure flags;
- Phoenix trace: LLM spans, tool spans, latency gaps, retries, provider errors;
- token usage: prompt/completion totals, tokens per verified source, tokens per
  report KB, extra completion after artifact exists;
- artifact files: `research/report.md`, `research/sources.jsonl`,
  `tool-results/*`;
- replay: reload session and verify artifact metadata survives.

The live gate should produce a small markdown scorecard per scenario:

```text
scenario: deep-research-long-report
status: pass
tool_chain: todo_write -> web_search -> web_fetch -> file_write -> file_edit -> todo_write -> final
usage: prompt=13700 completion=4817 total=18517
artifact_updates: 2
verified_sources: 3 domains=3
failure_flags: none
notes: no full rewrite after stale todo repair
```

## Efficiency Budgets

Initial soft budgets, tuned after baseline runs:

- final chat answer after artifact exists: <= 2,000 chars;
- max assistant message before artifact capture: <=
  `deep_research_inline_answer_max_chars`;
- repeated identical tool args: 0 for `web_fetch`, <= 1 for `web_search`;
- full report rewrites after initial write: 0;
- artifact updates before final: >= 1;
- verified source domains for deep mode: >= 2;
- `bash` in default Deep Research preset: 0;
- token regression: candidate run should stay within +20 percent of baseline
  for the same scenario unless quality improves and the scorecard records why.

## Acceptance Tests

1. A deep research run creates a per-session workspace immediately.
2. The first long synthesis writes `research/report.md`.
3. The frontend shows the report artifact while the run is still active.
4. If a todo remains open after a long draft, the next step updates todo state
   or patches the artifact; it does not re-stream the whole draft.
5. Reconnecting/replaying the session reconstructs artifact metadata.
6. Attempts to write outside the session workspace are denied.
7. Large tool results are spilled to files and can be read back with
   `read_file`.
8. Final answer is concise and references the artifact, while the full report
   is visible/downloadable from the UI.

## Recommended Default Tool Preset

Add a new preset instead of overloading `workspace`:

```python
if preset == "deep_research":
    return CliToolConfig(
        tools_mode="default",
        tools=("agent_tool", "skill_tool", "skill_view"),
        tool_packs=(
            "web",
            "planning_progress",
            "filesystem_read",
            "filesystem_write",
        ),
        allow_dangerous_tools=True,
        enable_python=False,
    )
```

Then enforce policy at the tool gate:

- `file_write`, `file_edit`, `file_patch`: allowed only under session workspace;
- `bash`: not included;
- `python`: opt-in only for numeric/data analysis prompts;
- `web_fetch`: allowed with source ledger tracking.

## Design Principle

Deep Research should behave like a careful writer with a working directory:
search and notes are tools, the report is a file, the chat is progress and final
handoff. The model should never need to spend thousands of tokens rewriting a
draft just because runtime state and visible todo state temporarily disagree.

## Implementation Slice P0 - 2026-05-31

Implemented first vertical slice:

- `deep_research` chat preset upgrades `deep_parallel_research` runs to a scoped
  tool surface: `web`, `planning_progress`, `filesystem_read`,
  `filesystem_write`, plus `agent_tool`, `skill_tool`, `skill_view`;
- `shell`, `python`, and broad `discovery` are not part of the default deep
  research preset;
- long inline drafts in Deep Research mode are captured to
  `research/report.md` before contract-repair continuation, so the next LLM turn
  receives a compact artifact pointer instead of the full draft;
- if `research/report.md` was written through `file_write`, finalization observes
  the existing report and records artifact metadata;
- stale todo repair after a report exists forces `todo_write` instead of
  `force_final_answer`, avoiding the destructive rewrite loop;
- `run_completed` and persisted assistant metadata carry
  `deep_research_artifacts`;
- the chat frontend parses that metadata and shows the report path/size in the
  existing Deep Research panel, including replay/session reload paths.

Focused checks:

- `tests/runtime/test_deep_research_artifacts.py`;
- `tests/runtime/test_research_contract_repair_tool_choice.py`;
- `examples/chat-demo/backend/tests/test_message_metadata.py`;
- focused synchronous `examples/chat-demo/backend/tests/test_tools.py` preset
  checks;
- frontend `vitest` suite and `tsc -b`.

## Implementation Slice P1-mini - 2026-05-31

Added live artifact instrumentation and scorecard signals:

- `file_write` and `file_edit` structured outputs now expose
  `created`/`existed_before` so the runtime can distinguish first writes from
  patches;
- runtime projects successful workspace file writes into `artifact_created` and
  `artifact_updated` events with relative path, kind, operation, size, tool name
  and tool call id;
- `DeepResearchPanel` updates from live artifact events as well as final
  `deep_research_artifacts` metadata;
- trace summary exposes `tool_chain`, aggregated `llm.usage`, and an
  `artifacts` block with update counts and `research/report.md` coverage.

Focused checks:

- `tests/runtime/test_artifact_events.py`;
- `tests/tools/test_builtin_filesystem_tools.py`;
- `examples/chat-demo/backend/tests/test_run_trace_summary.py`;
- frontend `tests/deepResearchEvents.test.ts`;
- frontend `tsc -b`.

## Implementation Slice P1-visible - 2026-05-31

Added the first user-visible workspace artifact surface:

- backend exposes bounded session artifact APIs:
  `GET /api/workspace/{session_id}/artifacts` and
  `GET /api/workspace/{session_id}/artifacts/{artifact_path:path}`;
- artifact listing is path-jailed to the session workspace and returns stable
  path, kind, size, and modified time metadata;
- artifact preview returns bounded text content and a `truncated` flag so the UI
  can inspect `research/report.md` without loading arbitrary large files into
  chat state;
- chat UI now has an artifact popover beside the run selector, with a compact
  artifact list and markdown/text preview area;
- `research/report.md` is prioritized when present, matching the Deep Research
  artifact contract.

Focused checks:

- `examples/chat-demo/backend/tests/test_workspace.py`;
- frontend `tests/WorkspaceArtifactsPanel.test.tsx`;
- frontend `tsc -b`.

## Implementation Slice P2-scorecard - 2026-05-31

Added deterministic trace-summary diagnostics for Deep Research efficiency:

- `research_efficiency` reports whether an artifact-backed Deep Research run was
  expected, the first tool used, full tool chain, repeated non-search tools,
  report update counts, assistant character count, total/output tokens, and
  output tokens spent after the first `research/report.md` update;
- summary failures now flag three token-waste patterns:
  `deep_research_no_report_artifact`,
  `deep_research_missing_initial_todo`, and
  `deep_research_long_final_after_report`;
- the heuristic is intentionally narrow: ordinary sourced reports may still
  finish in chat, while explicit `deep research`/`глубок...` tasks and task
  contracts can require the artifact path.

Focused checks:

- `examples/chat-demo/backend/tests/test_run_trace_summary.py`;
- `tests/observability/test_run_trace_summary.py`;
- `tests/cli/test_eval_harness.py`.

## Implementation Slice P2-cli-report - 2026-05-31

Projected the Deep Research efficiency diagnostics into CLI eval artifacts:

- `EvalSummary` now carries aggregated `llm_usage` and the
  `research_efficiency` block from run-trace summary;
- CLI eval `report.md` prints tool chain, input/output/total token counts,
  tokens emitted after the first `research/report.md` update, artifact expected
  state, report update count, and first tool;
- Deep Research trace failures are copied into eval `bug_tags`, and efficiency
  becomes `fail` when the run misses the artifact contract, skips the initial
  todo, or emits a long final duplicate after the report exists.

Focused checks:

- `tests/cli/test_eval_harness.py`;
- `examples/chat-demo/backend/tests/test_run_trace_summary.py`;
- `tests/observability/test_run_trace_summary.py`.

## Implementation Slice P2-live-scenario - 2026-05-31

Added the first explicit eval scenario for artifact-backed Deep Research:

- `deep_research_artifact_report` lives in the `deep` suite;
- it requires `todo_write -> web_search -> web_fetch -> file_write` and forbids
  `bash`/`python`;
- it runs with planning, web, filesystem read, and filesystem write packs in a
  sandboxed workspace;
- the expected final answer must point to `research/report.md`, letting the
  eval scorecard judge whether the expensive report stayed in the artifact
  instead of being duplicated in chat.

Focused checks:

- `tests/cli/test_eval_suite_membership.py`;
- `tests/cli/test_eval_harness.py`.

## Implementation Slice P3-live-probe - 2026-05-31

Extended the chat-demo Playwright probe for artifact-backed Deep Research:

- added deterministic fake provider scenario `deep_research_artifact`, which
  calls `todo_write`, `web_search`, two `web_fetch` reads, and `file_write` to
  create `research/report.md`;
- `chat_live_probe.py --scenario deep-research-artifact` now sends
  `research_depth=deep_parallel_research` and `tool_preset=deep_research`;
- the probe checks trace-summary failures, `research_efficiency`, artifact
  paths in trace, workspace artifact listing, bounded preview content, and the
  browser Artifacts panel;
- artifacts saved under the probe output now include `workspace-artifacts.json`
  and `workspace-preview.json` alongside screenshot, transcript, and
  `trace-summary.json`.

Focused checks:

- `examples/chat-demo/backend/tests/test_chat_deep_research_sse.py`;
- `examples/chat-demo/frontend/tests/e2e/chat_live_probe.py --scenario
  deep-research-artifact` against local fake backend/frontend on isolated ports.

## Implementation Slice P3-scorecard-cleanup - 2026-05-31

Fixed trace-summary tool accounting after the first live probe:

- `tool_names` and `tool_payloads` now prefer `tool_call_completed` payloads and
  only fall back to `tool_call_started` when no completed payload exists;
- this prevents scorecards from double-counting a single tool execution as both
  started and completed;
- the deterministic Deep Research probe now reports the expected chain:
  `todo_write -> web_search -> web_fetch -> web_fetch -> file_write`,
  `tool_calls=5`, `search_count=1`, and `fetch_count=2`.

Focused checks:

- `tests/observability/test_run_trace_summary.py`;
- `examples/chat-demo/backend/tests/test_run_trace_summary.py`;
- `tests/cli/test_eval_harness.py`;
- `examples/chat-demo/frontend/tests/e2e/chat_live_probe.py --scenario
  deep-research-artifact` against local fake backend/frontend on isolated ports.

## Implementation Slice P3-probe-scorecard - 2026-05-31

Added a human-readable markdown scorecard to each chat-demo live probe artifact
directory:

- `scorecard.md` summarizes run id, verdict, terminal event, failures, tool
  chain, input/output/total tokens, tokens after first report update, research
  search/fetch/domain counts, artifact paths, preview size/truncation, and
  Deep Research efficiency flags;
- the scorecard is written next to `trace-summary.json`, `workspace-artifacts.json`,
  `workspace-preview.json`, transcript excerpt, and screenshot;
- the deterministic `deep-research-artifact` probe now produces a compact report
  suitable for quick regression review without opening raw JSON.

Latest deterministic probe scorecard:

```text
tool_chain: todo_write -> web_search -> web_fetch -> web_fetch -> file_write
tokens: input=2922, output=72, total=144, after_report=48
research: search=1, fetch=2, domains=2, readiness=allowed
artifacts: trace=research/report.md, workspace=research/report.md
```

Focused checks:

- `examples/chat-demo/frontend/tests/test_chat_live_probe_budget.py`;
- `examples/chat-demo/frontend/tests/e2e/chat_live_probe.py --scenario
  deep-research-artifact` against local fake backend/frontend on isolated ports.

## Implementation Slice P4-artifact-tools - 2026-05-31

Added model-visible workspace artifact tools:

- `artifact_list` lists known durable artifacts under `research/` and
  `tool-results/` with path, kind, size, and mtime;
- `artifact_read` reads bounded UTF-8 artifact content by workspace-relative
  artifact path;
- `artifact_preview` returns bounded preview text plus markdown headings for
  quick report-state checks;
- all three tools are read-only, workspace-scoped, and reject non-artifact paths
  so they do not become a second unrestricted `read_file`;
- added a new `artifacts` tool pack and included it in the chat-demo
  `deep_research` preset and the CLI `deep_research_artifact_report` scenario;
- deep research prompt reminders now tell the model to use `artifact_list` or
  `artifact_preview` before final handoff.

Focused checks:

- `tests/tools/test_builtin_filesystem_tools.py`;
- `tests/tools/test_toolset.py`;
- `tests/tools/test_toolset_docs_sync.py`;
- `tests/tools/test_builtin_registry.py`;
- `examples/chat-demo/backend/tests/test_tools.py`;
- `examples/chat-demo/backend/tests/test_chat_deep_research_sse.py`.

## Implementation Slice P5-file-patch - 2026-05-31

Added `file_patch` as a batched exact-replacement workspace write tool:

- applies multiple ordered `old_text` -> `new_text` replacements to one UTF-8
  file in a single call;
- checks `expected_occurrences` per patch before committing the final file;
- supports `dry_run`, bounded previews, and `max_bytes` like `file_edit`;
- returns structured `operation=patch`, total `replacements`, and per-patch
  replacement metadata for trace analysis;
- is included in `filesystem_write`, artifact event projection, and the deep
  research prompt reminder so agents can revise several report sections without
  full rewrites.

Focused checks:

- `tests/tools/test_builtin_filesystem_tools.py`;
- `tests/tools/test_toolset.py`;
- `tests/tools/test_toolset_docs_sync.py`;
- `tests/tools/test_builtin_registry.py`;
- `tests/runtime/test_artifact_events.py`;
- `tests/runtime/test_llm_step_system_prompt.py`.

## Implementation Slice P6-durable-source-ledger - 2026-05-31

Persisted the Deep Research source ledger into the session workspace:

- web search/fetch ledger updates now write `research/sources.jsonl` when Deep
  Research artifact mode is active;
- the JSONL file contains verified reads, blocked/failed reads, search
  candidates, and assistant-visible links with `ledger_section` and
  `ledger_index` metadata;
- source ledger artifact metadata is attached to `deep_research_artifacts`;
- runtime emits `artifact_created`/`artifact_updated` for
  `research/sources.jsonl`, so the frontend artifact panel can expose the
  evidence ledger alongside `research/report.md`;
- `source_ledger_updated` events include a compact artifact pointer for
  scorecards and live probes.

Focused checks:

- `tests/runtime/test_deep_research_artifacts.py`;
- `examples/chat-demo/backend/tests/test_chat_deep_research_sse.py`;
- `examples/chat-demo/frontend/tests/e2e/chat_live_probe.py --scenario
  deep-research-artifact`.

## Implementation Slice P7-source-ledger-scorecard - 2026-05-31

Added source-ledger artifact checks to trace summaries and live scorecards:

- `artifacts` summary now tracks whether `research/sources.jsonl` was updated
  and the highest observed `record_count`;
- `research_efficiency` exposes `missing_source_ledger_artifact`,
  `source_ledger_update_count`, and `source_ledger_record_count`;
- Deep Research failures include `deep_research_no_source_ledger_artifact`;
- the Playwright live probe fails Deep Research scenarios when the source
  ledger artifact is missing and prints source record count in `scorecard.md`.

Focused checks:

- `examples/chat-demo/backend/tests/test_run_trace_summary.py`;
- `examples/chat-demo/frontend/tests/test_chat_live_probe_budget.py`;
- `examples/chat-demo/frontend/tests/e2e/chat_live_probe.py --scenario
  deep-research-artifact`.

## Implementation Slice P14.3-search-budget-diagnostics - 2026-05-31

Added the measurement layer for adaptive-but-bounded discovery before adding a
hard phase controller:

- trace summaries now expose Deep Research search budget diagnostics:
  `search_initial_budget`, `search_hard_cap`, `search_budget_status`,
  `discovery_expansion_count`, `search_call_count`, `fetch_attempt_count`,
  `repeated_search_query_count`, and repeated normalized queries;
- `deep_research_repeated_search_args` fires when identical web search queries
  are repeated instead of refined or followed by fetches;
- `deep_research_search_without_fetch_progress` fires when discovery expands
  beyond the initial budget without fetch attempts or source-ledger progress;
- `deep_research_tool_entropy_high` fires when the hard search cap is exceeded
  or a large tool loop happens with no evidence progress;
- adaptive expansion is explicitly allowed when there is fetch/ledger progress,
  so "six sources" remains an initial budget, not a correctness assumption;
- live Deep Research scorecards now show search budget status and repeated query
  counts, and Playwright acceptance treats these new flags as forbidden.

Focused checks:

- `examples/chat-demo/backend/tests/test_run_trace_summary.py`;
- `examples/chat-demo/frontend/tests/test_chat_live_probe_budget.py`.

## Implementation Slice P14.4-soft-phase-contract - 2026-05-31

Started the phase-controller layer as a soft runtime contract before enforcing
hard tool gating:

- `ResearchSessionContract` now computes a Deep Research phase:
  `plan`, `discover`, `verify`, `write`, `review`, or `final`;
- each phase carries `next_allowed_tools` so the model sees the intended
  narrow tool surface for the next step;
- the phase payload is included under `research_session_contract.deep_research`
  and persisted in terminal metadata when available;
- chat runtime attachments now include a compact `deep_research_phase_contract`
  reminder with the current phase and preferred next tools;
- trace summaries expose `research_efficiency.deep_research_phase` and
  `deep_research_phase_next_allowed_tools`;
- live scorecards print the phase in the Deep Research row.

This is intentionally a soft contract: it guides and measures tool order first.
Hard runtime gating can follow once the fake/live lanes confirm the phase
classifier is stable across normal, fallback, and repair runs.

Focused checks:

- `tests/runtime/test_research_session_contract.py`;
- `tests/runtime/test_llm_step_system_prompt.py`;
- `examples/chat-demo/backend/tests/test_run_trace_summary.py`;
- `examples/chat-demo/frontend/tests/test_chat_live_probe_budget.py`.

## Implementation Slice P14.5-phase-violation-diagnostics - 2026-05-31

Added trace-level phase violation checks before hard runtime gating:

- trace summaries now reconstruct the expected Deep Research phase sequence
  from tool/artifact events;
- valid default flow is treated as:
  `todo_write -> discover tools -> web_fetch attempts -> file_write ->
  review/edit tools -> final`;
- `deep_research_phase_violation` fires when a tool is used outside the current
  phase, for example writing `research/report.md` before fetch verification;
- `research_efficiency.phase_violations` records the offending phase, tool, and
  allowed tool set;
- live Deep Research acceptance treats phase violations as forbidden;
- live scorecards print `phase_violations` beside the current phase.

This keeps the runtime non-disruptive while making entropy visible in the same
scorecard used for fake and real-provider lanes. The next implementation step
can use these diagnostics as the acceptance baseline for optional hard gating.

Focused checks:

- `examples/chat-demo/backend/tests/test_run_trace_summary.py`;
- `examples/chat-demo/frontend/tests/test_chat_live_probe_budget.py`;
- `examples/chat-demo/frontend/tests/e2e/chat_live_probe.py --scenario
  deep-research-artifact`;
- `examples/chat-demo/frontend/tests/e2e/chat_live_probe.py --scenario
  deep-research-blocked-fetch`.

## Implementation Slice P8-full-report-rewrite-guard - 2026-05-31

Added trace-level detection for the original waste loop class:

- artifact events now preserve `file_write` mode (`overwrite` vs `append`);
- trace summaries count full `file_write` updates to `research/report.md`;
- Deep Research efficiency now exposes `report_full_write_count` and
  `full_report_rewrite`;
- failure flag `deep_research_full_report_rewrite` fires when a Deep Research
  run fully writes `research/report.md` more than once;
- append writes are not counted as full rewrites, but targeted `file_edit` and
  `file_patch` remain the preferred path after the initial draft;
- live scorecards print `full_writes` next to artifact update counts.

Focused checks:

- `tests/runtime/test_artifact_events.py`;
- `examples/chat-demo/backend/tests/test_run_trace_summary.py`;
- `examples/chat-demo/frontend/tests/test_chat_live_probe_budget.py`;
- `examples/chat-demo/frontend/tests/e2e/chat_live_probe.py --scenario
  deep-research-artifact`.

## Implementation Slice P9-stale-report-edit-guard - 2026-05-31

Added trace-level stale-read detection for targeted report edits:

- trace summary now treats `read_file`, `artifact_read`, and `artifact_preview`
  of `research/report.md` as a fresh read;
- every `file_write` to `research/report.md` invalidates freshness;
- `file_edit`/`file_patch` updates to the report without a fresh read are
  counted as stale targeted edits;
- Deep Research efficiency exposes `report_targeted_edit_count`,
  `report_targeted_edit_without_fresh_read_count`, and `stale_report_edit`;
- failure flag `deep_research_stale_report_edit` catches report patches that
  risk overwriting a newer draft section from stale context;
- live scorecards print `stale_edits`.

Focused checks:

- `examples/chat-demo/backend/tests/test_run_trace_summary.py`;
- `examples/chat-demo/frontend/tests/test_chat_live_probe_budget.py`;
- `examples/chat-demo/frontend/tests/e2e/chat_live_probe.py --scenario
  deep-research-artifact`.

## Implementation Slice P10-repeated-report-read-signal - 2026-05-31

Added repeated unchanged report-read detection:

- trace summary tracks a report generation counter for `research/report.md`;
- `read_file`, `artifact_read`, and `artifact_preview` of the same report
  generation count as duplicate reads after the first one;
- writes/patches/edits advance the generation, so reading after an update is
  allowed and useful;
- Deep Research efficiency exposes `repeated_unchanged_report_read_count` and
  `repeated_report_read`;
- failure flag `deep_research_repeated_report_read` marks wasteful rereads of
  unchanged report content;
- live scorecards print `repeat_reads`.

Focused checks:

- `examples/chat-demo/backend/tests/test_run_trace_summary.py`;
- `examples/chat-demo/frontend/tests/test_chat_live_probe_budget.py`;
- `examples/chat-demo/frontend/tests/e2e/chat_live_probe.py --scenario
  deep-research-artifact`.

## Implementation Slice P11-tool-result-workspace-spill - 2026-05-31

Connected oversized tool output spill to the session workspace:

- existing executor spill still persists the full payload to `ArtifactStore`
  and returns a small model-visible preview;
- when a workspace is active, the same encoded payload is mirrored to
  `tool-results/<tool_call_id>.json`;
- the in-context replacement includes `workspace_artifact_path` and
  `persisted_artifact.workspace_path`;
- `tool_call_completed` rows expose the persisted artifact pointer and preview
  path, letting traces and UI diagnostics route users to the durable file;
- fixed spill metadata to use the actual `tool_call_id` instead of the runtime
  attempt id for filenames.

Focused checks:

- `tests/tools/test_output_spill.py`.

## Implementation Slice P12-final-handoff-and-patch-lane - 2026-05-31

Closed the remaining final-handoff acceptance gap and added a second
deterministic Deep Research live lane:

- Deep Research efficiency now checks whether the final answer points users to
  `research/report.md` or clearly says the full report is saved there;
- failure flag `deep_research_final_missing_report_reference` catches final
  handoffs that leave the durable report hidden;
- live scorecards print `final_refs_report`;
- added fake provider scenario `deep_research_targeted_patch`, which writes the
  report, previews it, and applies a targeted `file_patch`;
- added Playwright scenario `deep-research-targeted-patch` to verify the
  efficient edit path (`file_write -> artifact_preview -> file_patch`) without
  stale edits, repeated reads, or full rewrites;
- report-flow analysis now aligns artifact updates with `tool_call_id`, so
  parallel tool batches preserve logical tool order in trace diagnostics.

Focused checks:

- `examples/chat-demo/backend/tests/test_run_trace_summary.py`;
- `examples/chat-demo/frontend/tests/test_chat_live_probe_budget.py`;
- `examples/chat-demo/backend/tests/test_chat_deep_research_sse.py`;
- `examples/chat-demo/frontend/tests/e2e/chat_live_probe.py --scenario
  deep-research-artifact`;
- `examples/chat-demo/frontend/tests/e2e/chat_live_probe.py --scenario
  deep-research-targeted-patch` with `CHAT_DEMO_FAKE_SCENARIO=deep_research_targeted_patch`.

## Implementation Slice P13-blocked-fetch-and-live-scorecards - 2026-05-31

Closed the blocked-source fallback lane and made live scorecards more useful
for debugging entropy:

- `web_fetch` now supports deterministic mock HTTP payloads via
  `mock_status_code`, `mock_content`, and `mock_content_type`, so tests can
  exercise HTTP 403/429/451 behavior without depending on external sites;
- research evidence treats structured `blocked: true`, unavailable payloads,
  and HTTP `4xx/5xx` status codes as failed reads, while blocked HTTP statuses
  are also placed in `source_ledger.blocked_reads`;
- trace research summaries now distinguish successful `fetch_count` from
  `fetch_attempt_count` and `failed_fetch_count`;
- source-verified/deep runs with enough blocked fetch attempts and zero
  successful reads enter `fetch_fallback_required` instead of being mislabeled
  as `search_only_research_report`;
- added fake provider scenario `deep_research_blocked_fetch`;
- added Playwright scenario `deep-research-blocked-fetch`, which verifies
  `todo_write -> web_search -> web_fetch -> web_fetch -> file_write`, a visible
  report artifact, source ledger artifact, and explicit caveat in
  `research/report.md`;
- live scorecards now print fetch attempts alongside successful fetches and
  persist `/api/health` to `health.json`;
- scorecards include Phoenix tracing status as `phoenix: enabled/configured/error`.
  The fake local runs keep Phoenix disabled, but the same scorecard field will
  surface tracing setup problems when the Docker/Phoenix stack is enabled.

Current live ladder:

1. `deep-research-artifact`: baseline durable report write.
2. `deep-research-targeted-patch`: efficient preview plus targeted patch.
3. `deep-research-blocked-fetch`: blocked-source fallback with explicit caveat.

Focused checks:

- `tests/tools/test_builtin_web_tools.py`;
- `tests/runtime/test_research_session_contract.py`;
- `examples/chat-demo/backend/tests/test_run_trace_summary.py`;
- `examples/chat-demo/frontend/tests/test_chat_live_probe_budget.py`;
- `examples/chat-demo/backend/tests/test_chat_deep_research_sse.py`;
- `examples/chat-demo/frontend/tests/e2e/chat_live_probe.py --scenario
  deep-research-artifact`;
- `examples/chat-demo/frontend/tests/e2e/chat_live_probe.py --scenario
  deep-research-targeted-patch`;
- `examples/chat-demo/frontend/tests/e2e/chat_live_probe.py --scenario
  deep-research-blocked-fetch`.

## Corrective Plan P14-real-model-regression - 2026-05-31

This section supersedes the earlier assumption that Deep Research can be made
reliable by exposing a larger tool bag plus trace guards. The real OpenRouter
run showed that deterministic fake lanes are not enough: the model can hit a
tool-policy edge case, recover chaotically, and still produce a plausible
preliminary report.

Observed regression:

- `skill_tool` was called with `base_dir=/workspace/agent_driver/skills/curated`
  while the active session workspace jail was
  `/workspace/examples/chat-demo/workspace/session_*`;
- the trusted curated skill path was therefore denied as outside workspace;
- after the denied skill lookup, the model retried skill discovery, expanded
  into repeated `web_search`, and spawned delegated work through `agent_tool`;
- `research/report.md` existed, but the Deep Research panel showed `0 verified`;
- the chat answer looked like a useful article even though it explicitly said
  pages still needed verification;
- token usage rose quickly while the run spent work on recovery, repeated
  search, and delegation instead of controlled verification plus artifact
  synthesis.

Root cause:

- Deep Research is still too close to free-form ReAct.
- The default tool surface includes too many recovery paths (`agent_tool`,
  skill discovery, broad web search) at the same time.
- Curated skills are runtime assets, but `skill_tool` treats their absolute path
  like ordinary workspace filesystem input.
- Report artifact existence is treated as a positive signal even when source
  coverage is still preliminary.
- Fake/live lanes validated ideal tool order, but not denied-tool recovery,
  real-model over-delegation, or preliminary-final behavior.

Recommended architecture:

Deep Research should become a small runtime state machine that uses the LLM for
judgment and writing, while the runtime controls phase, allowed tools, budgets,
and readiness.

Default mode: `deep_research`

- no `agent_tool` by default;
- no shell/python by default;
- curated research skill loaded by name or attached by runtime, not by asking
  the model to construct an absolute path;
- parent run owns source selection, verification, report writing, and final
  handoff;
- subagents move to a separate opt-in mode,
  `deep_parallel_research_with_workers`, after the base lane is stable.

Phases and tool surface:

1. `setup`
   - allowed: `todo_write`, `skill_view` by name;
   - goal: create visible todo plan, load the research workflow;
   - failure if curated skill lookup is denied or retried with an absolute path.

2. `discovery`
   - allowed: `web_search`;
   - starts with a small query budget, but the budget is adaptive rather than
     fixed;
   - if source coverage is still weak after the first pass, the controller may
     open a bounded expansion pass with new query angles and excluded already
     covered domains;
   - bounded by expansion count, repeated-query guard, and domain dedupe;
   - output is candidate URLs only, never verified evidence.

3. `verification`
   - allowed: `web_fetch`;
   - fetch selected candidate URLs from diverse domains;
   - classify each read as `verified`, `blocked`, or `failed`;
   - if pages block access, enter explicit fallback mode instead of retry loops.

4. `workspace_draft`
   - allowed: `file_write`, `artifact_preview`;
   - write `research/report.md` and `research/sources.jsonl`;
   - long synthesis belongs in the report artifact, not inline chat.

5. `audit_patch`
   - allowed: `artifact_preview`, `read_file`, `file_patch`;
   - patch the report after preview/read;
   - repeated full `file_write` to `research/report.md` remains a failure.

6. `final_handoff`
   - allowed: final answer only;
   - answer must be short, link to `research/report.md`, state verified source
     count, and include fallback caveats when applicable.

Report status model:

- `draft`: report exists but verification is incomplete;
- `verified`: required source coverage is satisfied;
- `fallback`: enough fetch attempts were blocked/failed and the report clearly
  states the limitation;
- `invalid`: report exists but source ledger is missing or contradicts the
  final handoff.

The UI must not present a report with `0 verified` as a completed Deep Research
result unless it is explicitly marked `fallback`.

Implementation work plan:

P14.1 Trusted curated skills

- Treat bundled curated skills as trusted runtime assets.
- Allow `skill_tool`/`skill_view` to read trusted roots outside the session
  workspace jail.
- Keep untrusted absolute paths outside workspace denied.
- Prefer `skill_view(name="deep-research-report")` or runtime attachment over
  model-generated absolute paths.

P14.2 Default deep research tool surface

- Remove `agent_tool` from the default `deep_research` preset.
- Disable runtime subagent execution for default Deep Research.
- Remove prompt/runtime recommendations that encourage delegation in the base
  mode.
- Add a separate opt-in preset for worker-based parallel research later.

P14.3 Phase controller

- Persist `deep_research_phase` in run metadata.
- Compute phase transitions from trace/evidence state, not only model text.
- Restrict allowed tools per phase.
- Add adaptive budgets:
  - initial search budget, e.g. 3-6 queries for normal tasks;
  - controlled expansion budget, e.g. another 3-6 queries only when coverage
    gaps are explicit;
  - hard search cap per run/profile to prevent infinite discovery loops;
  - repeated search query count;
  - fetch attempts;
  - output tokens after first report artifact update.
- Make the transition out of `discovery` depend on source coverage, not on a
  fixed number of sources. Coverage includes verified reads, distinct domains
  or publishers, missing plan aspects, and whether primary sources are required
  for the task type.

P14.4 Readiness and report status

- Final readiness must include report status and source ledger consistency.
- A report with `0 verified` is not complete unless fallback criteria are met.
- Final answer is blocked or repaired if it presents preliminary material as a
  final report.

P14.5 Observability failures

Add trace failure flags:

- `deep_research_skill_denied`;
- `deep_research_unexpected_agent_tool`;
- `deep_research_preliminary_final`;
- `deep_research_low_verified_coverage`;
- `deep_research_tool_entropy_high`;
- `deep_research_repeated_search_args`;
- `deep_research_search_without_fetch_progress`.

Testing plan:

1. Unit tests
   - trusted curated skill outside workspace is allowed;
   - untrusted outside-workspace skill path is denied;
   - default `deep_research` preset excludes `agent_tool`;
   - deep report status is `draft`, `verified`, `fallback`, or `invalid`
     based on source ledger state.

2. Runtime contract tests
   - final readiness denies a report with `0 verified` and no fallback;
   - repeated search without fetch progress triggers repair;
   - denied skill lookup does not lead to repeated skill retries;
   - phase controller chooses the next allowed tool set deterministically.

3. Fake regression lanes
   - `deep_research_skill_denied_recovery`: model attempts denied curated path;
     expected behavior is runtime repair or safe skill load by name, not chaotic
     search/delegation;
   - `deep_research_unexpected_agent_tool`: fake provider tries `agent_tool`;
     expected behavior is tool unavailable or flagged failure in default mode;
   - `deep_research_preliminary_final`: report exists with candidates but no
     verified reads; expected status is `draft`, not completed final.

4. Playwright fake live lanes
   - assert tool order by phase;
   - assert artifact panel shows report status and verified/fallback counts;
   - assert scorecard captures phase transitions, budgets, tool entropy, and
     failure flags.

5. Real-model live lanes
   - run the original fork-join prompt against the real OpenRouter provider;
   - require no `agent_tool` in default mode;
   - require adaptive-but-bounded search: expansion is allowed only when the
     scorecard records explicit coverage gaps and new query angles;
   - require at least two fetch attempts unless fallback is reached;
   - require `research/report.md`, `research/sources.jsonl`, short final
     handoff, and accurate report status;
   - persist Playwright screenshots, trace summary, workspace artifacts,
     scorecard, and Phoenix trace status.

6. Phoenix/deep evaluation
   - score tool sequence, token usage, repeated search args, failed/blocked
     fetches, source coverage, and output tokens after first report write;
   - compare real-provider runs against fake lanes so deterministic tests do not
     hide real-model recovery failures.

Acceptance criteria:

- default Deep Research follows this shape:
  `todo_write -> skill_view -> web_search* -> web_fetch* -> file_write ->
  artifact_preview/read_file -> optional file_patch -> final`;
- no `agent_tool` in default Deep Research;
- no completed final with `0 verified` unless fallback status is explicit;
- no long final answer after `research/report.md` exists;
- no repeated full rewrite of `research/report.md`;
- no fixed "six sources is enough" assumption: final readiness is based on
  evidence coverage and fallback status, while search expansion remains bounded;
- live OpenRouter fork-join prompt passes the same scorecard used by fake lanes.

## Implementation Slice P14.1-trusted-skills-and-default-surface - 2026-05-31

Started the corrective plan with the two changes that directly address the real
OpenRouter regression:

- `skill_tool` and `skill_view` may now read an explicitly trusted skill root
  outside the session workspace jail, allowing bundled curated skills such as
  `deep-research-report` to work from per-session workspaces;
- untrusted absolute paths outside the session workspace remain denied;
- the default `deep_research` chat preset no longer exposes `agent_tool`;
- runtime subagent execution is disabled for the default `deep_research` preset;
- Deep Research contract hints no longer recommend delegation as the pressure
  strategy in the base mode;
- contract-repair nudges list workspace/file tools instead of encouraging
  `agent_tool`;
- trace summaries now flag `deep_research_unexpected_agent_tool` and
  `deep_research_skill_denied`;
- Playwright Deep Research scorecards treat those new failures as forbidden.

Focused checks:

- `tests/tools/test_builtin_skill_tools.py`;
- `tests/runtime/test_research_session_contract.py`;
- `examples/chat-demo/backend/tests/test_tools.py`;
- `examples/chat-demo/backend/tests/test_run_trace_summary.py`.

## Implementation Slice P14.2-report-status-guard - 2026-05-31

Added the first report-status guard so a visible report artifact cannot hide
weak evidence coverage:

- trace summaries now derive Deep Research report status:
  `missing`, `invalid`, `draft`, `verified`, `fallback`, or `not_applicable`;
- status uses the durable report artifact, source ledger artifact,
  `source_ledger_updated` counts, required verified reads, and fetch fallback
  state;
- `research_efficiency` exposes verified, blocked, failed, and candidate counts;
- `deep_research_low_verified_coverage` fires when a report exists but remains
  draft/pre-verified;
- `deep_research_preliminary_final` fires when a draft report is handed off as
  final rather than explicitly preliminary;
- live scorecards print `status` and `verified` in the Deep Research row;
- Deep Research Playwright scenarios forbid low verified coverage unless the run
  is explicitly in fallback mode.

Focused checks:

- `examples/chat-demo/backend/tests/test_run_trace_summary.py`;
- `examples/chat-demo/frontend/tests/test_chat_live_probe_budget.py`;
- `examples/chat-demo/frontend/tests/e2e/chat_live_probe.py --scenario
  deep-research-artifact`.
