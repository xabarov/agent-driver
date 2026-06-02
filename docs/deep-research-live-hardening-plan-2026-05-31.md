# Deep Research Live Hardening Plan

Status: active execution plan, created 2026-05-31.

This plan is for the failure class observed in live chat-demo runs: the model
starts a visible plan, writes a long report-like answer in chat while the plan
is still `0/N`, the runtime asks for repair, and the model rewrites the same
large answer token by token. The goal is not to add more prompts and hope. The
goal is an artifact-first Deep Research runtime that is measurable in Phoenix,
Playwright screenshots, backend logs, and per-run trace summaries.

## Executive Decision

Use three Deep Research profiles, but do not confuse profiles with architecture
variants:

- `light`: single agent, web search plus web fetch only, short answer, no report
  artifact, no subagents.
- `medium`: parent orchestrator plus bounded subagents, durable
  `research/report.md`, source ledger, targeted file edits, and concise final
  chat answer.
- `hard`: medium plus verifier/auditor workers, claim-source matrix, broader
  source budget, optional computation, and PDF export from the report artifact.

The recommended Deep Research profile default is `medium` after the user has
chosen Deep Research. The composer itself should stay lightweight by default
(`chat` or `web`, depending on product settings) so ordinary questions do not
silently become expensive research runs. `light` is a fast lookup mode, not Deep
Research. `hard` is opt-in for broad, ambiguous, high-value questions where the
token cost earns its keep.

Architecture variants should be compared later, only after the basic
tool/artifact/trace contract is stable. Until then, failures mostly measure
contract mismatch rather than research quality.

## Current Truth In This Codebase

These facts come from local code inspection on 2026-05-31 and a re-check on
2026-06-02. Items marked fixed are no longer current blockers, but remain here
so old trace failures are interpreted correctly.

- `deep_research` preset currently enables `web`, `planning_progress`,
  `filesystem_read`, `filesystem_write`, `artifacts`, `skill_tool`, and
  `skill_view` in
  `examples/chat-demo/backend/app/services/agent_factory.py`.
- `filesystem_read` provides `read_file`, `glob_search`, and `grep_search`.
- `filesystem_write` provides `file_write`, `file_edit`, `file_patch`, and
  `notebook_edit`.
- `artifacts` provides `artifact_list`, `artifact_read`, and
  `artifact_preview`.
- Fixed on 2026-06-02: the backend now creates
  `SubagentSettings(enable_subagents=True, max_child_runs=...)`, and
  `deep_research` exposes `agent_tool`.
- Fixed on 2026-06-02: `deep-research-report/SKILL.md` now uses
  `agent_tool`, includes `read_file`, `file_write`, `file_edit`,
  `file_patch`, and names the canonical artifact tools
  `artifact_list`/`artifact_read`/`artifact_preview`.
- Remaining subagent blocker: the parent must consume child notes and create or
  patch parent-owned `research/report.md` plus `research/sources.jsonl` after
  child joins. Child-created artifacts and child long finals must not satisfy
  the medium readiness gate.
- The chat-demo workspace API currently has artifact list and preview endpoints
  only. There is no raw download endpoint and no Markdown-to-PDF export.
- There is no unified `source_read`, `pdf_read`, or `browser_read` tool yet.
  Evidence currently comes from `web_search`, `web_fetch`, and the source ledger
  reconstructed from their results.
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

OpenAI's Deep Research API is optimized around search/fetch-style data access,
not arbitrary tool use. For remote MCP data sources, the compatible interface is
explicitly `search` plus `fetch`; `max_tool_calls` is the primary cost/latency
control, and long tasks should run in background mode:
https://developers.openai.com/api/docs/guides/deep-research

Anthropic's Web Fetch tool is a useful design reference for `source_read`: it
is a dedicated URL-reading capability rather than a full browser, and Claude
Code permission rules can scope `WebFetch` by domain:
https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-fetch-tool
https://code.claude.com/docs/ru/settings

Anthropic citations documentation is the right reference for PDF handling:
PDFs should be extracted/chunked with page-aware citations when possible; scanned
PDFs without extractable text need a different fallback path and should not be
presented as citable text evidence:
https://platform.claude.com/docs/en/build-with-claude/citations

Browser control is materially riskier than fetch/read tools. Anthropic's Chrome
safety guidance calls out prompt injection from web content and recommends
starting with trusted sites and confirming sensitive/high-risk tasks:
https://support.claude.com/en/articles/12902428-using-claude-in-chrome-safely

Anthropic's multi-agent research writeup uses a lead agent that plans and
launches parallel subagents for independent search paths. It also warns by
example: multi-agent systems buy breadth and coverage with much higher token
usage, so they must be gated by task shape and measured:
https://www.anthropic.com/engineering/multi-agent-research-system

Anthropic's current multi-agent research article sharpens two constraints we
must keep: multi-agent shines on breadth-first research with independent search
paths, but it can burn roughly an order of magnitude more tokens than ordinary
chat. It also recommends explicit effort scaling rules, detailed delegation
prompts, and lead-agent synthesis after subagents return findings:
https://www.anthropic.com/engineering/multi-agent-research-system

Claude Code subagents are also a useful external UX/tool-surface reference:
subagents are separate context windows with focused prompts, specific tool
access, independent permissions, and are meant to keep high-volume search/log
output out of the main conversation:
https://code.claude.com/docs/en/sub-agents

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

Deep Research Bench is a useful benchmark design reference because it combines
multi-step web tasks, human-worked answers, a frozen RetroSearch environment,
and trace-level evaluation for hallucinations, tool use, and forgetting:
https://arxiv.org/abs/2506.06287

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
- `AgentTool` distinguishes fresh subagents from forks: fresh workers need a
  self-contained brief; fork-like workers inherit context but should still
  return only what the parent needs.
- Large tool results are persisted to session-local `tool-results` files with
  a preview and a path.
- The agent list can be injected as a message rather than embedded in the tool
  schema, which avoids cache churn from dynamic tool descriptions.
- `WebFetchTool` is read-only, concurrency-safe, validates URL shape, scopes
  permissions by hostname, supports provider-specific scraping, and applies a
  result-size cap.
- PDF utilities validate magic bytes, reject oversized/invalid PDFs before they
  enter model context, expose structured PDF errors, and use poppler tools for
  page count/render fallback.

Adopt:

- Read-before-edit/write for `research/report.md`.
- Prefer `file_patch`/`file_edit` after initial `file_write`.
- Keep subagent outputs summarized; parent synthesizes.
- Persist large tool outputs and expose only previews in context.
- Build `source_read` as a narrow read-only evidence tool that wraps ordinary
  URL fetch, PDF extraction, and later rendered-page fallback behind one ledger
  contract.
- Build `pdf_read` with OpenClaude-like validation, size limits, page ranges,
  structured errors, and optional page-image fallback for scanned documents.

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
- Browser tooling is a configurable provider-backed toolset, separate from
  ordinary web extraction. It can route through local Chromium, Browserbase,
  Browser Use, or Firecrawl-like providers.
- Browser tests explicitly cover secret exfiltration, snapshot redaction, SSRF,
  cloud metadata endpoints, redirect safety, and local-vs-cloud routing.

Adopt:

- Bounded fan-out: default 2-3 children for medium, 3-5 for hard.
- Flat subagents by default.
- Child toolsets are strict subsets of parent toolsets.
- Parent owns `research/report.md`; children write notes only or return compact
  summaries.
- Add stale artifact edit detection and repeated-read/search loop guards.
- Add large-result spill-to-file with a short preview before exposing tool
  outputs back to the model; do this for web/PDF/browser reads and subagent
  summaries before raising context budgets.
- Keep browser capability off by default and introduce it as hard-only fallback
  with provider isolation, URL safety, secret redaction, SSRF/IMDS blocks,
  screenshot artifacts, and strict step budgets.

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

These are workload and cost profiles, not competing architecture variants. The
same stable runtime contract should support all three profiles. The profiles
change budgets, allowed tool surface, source thresholds, subagent fan-out, and
artifact requirements.

Do not benchmark architecture variants until the Phase 0.5 contract triage gate
passes. A failed run with unknown tools, missing trace projection, or denied
phase tools tells us the runtime contract is broken; it does not tell us whether
one Deep Research architecture is better than another.

### Architecture Variants To Compare Later

After the contract is stable, compare these variants under the same
light/medium/hard profiles:

- Variant A: prompt-led ReAct with artifact tools. This is closest to the
  current system and is the baseline. The model sees the workflow instructions
  and tools, then decides the sequence.
- Variant B: controller-led artifact-first workflow. The runtime reserves
  report and ledger artifacts, owns phase transitions, inserts mandatory repair
  tool calls, and blocks long chat prose before the report exists.
- Variant C: research graph with role-specialized workers. The parent runs a
  fixed graph of discovery, verification, writing, and audit roles. This is the
  hard-mode candidate and should be tested only after Variant B is reliable.

Initial recommendation: stabilize Variant B for `medium`. Keep Variant A only
as a regression baseline, and delay Variant C until `source_read`, report
editing, subagent summaries, Phoenix links, and UI artifacts are reliable.

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
- Future `source_read` if implemented; otherwise use `web_fetch`.
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
- `source_read` when implemented: preferred evidence reader for URL/search
  result IDs.
- `pdf_read` when implemented: page-range extraction for PDF sources and source
  citation support.
- `browser_read` when implemented: rendered DOM/text/screenshot fallback for
  JS-heavy pages or fetch-blocked pages.

Hard-only tools that are not part of the default path:

- `browser_action` / full browser automation: navigate, click, type, scroll, or
  login-like workflows. This requires explicit opt-in, separate security gates,
  and live browser safety tests before release.

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
- If `source_read` exists, verified source count comes from `source_read`
  ledger records, not only raw `web_fetch` count.
- If `pdf_read` is used, claims cite page numbers or page ranges when the
  extractor can provide them.
- If `browser_read` is used, the scorecard records why `web_fetch`/`pdf_read`
  were insufficient and stores a screenshot/snapshot artifact.
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
- The model jumps to browser automation before trying search/fetch/PDF read.
- Browser or PDF output is cited as verified without ledger status and content
  location.
- PDF export is simulated in text instead of returning a file.

### Source Reading Ladder

Deep Research should not expose a large browser surface as the normal way to
read the web. The healthy path is a narrow evidence ladder:

1. `web_search`: find candidates and deduplicate domains.
2. `source_read`: read a candidate URL or search result ID and write a durable
   source ledger row. Internally it may call `web_fetch`, PDF extraction, or
   later rendered-page fallback, but the model sees one evidence contract.
3. `pdf_read`: use only when the source is a PDF or `source_read` classifies the
   URL as PDF/needs PDF extraction.
4. `browser_read`: use only when the page requires rendered DOM, JS execution,
   visual inspection, or fetch/PDF tools cannot provide enough evidence.
5. `browser_action`: use only for explicit hard-mode tasks that require
   interaction. It is not needed for ordinary source verification.

`source_read` output contract:

```text
source_read(url_or_source_id, purpose, max_chars?, page_range?)
  -> source_id: string
     url: string
     final_url: string
     title?: string
     kind: html | pdf | rendered_page | text | unknown
     status: verified | candidate | blocked | failed | partial
     evidence_text: string
     excerpt: string
     content_hash: string
     location_hints: [{ page?, section?, char_range? }]
     ledger_path: research/sources.jsonl
     artifact_path?: tool-results/... | research/source-cache/...
     screenshot_artifact_path?: ...
     warnings: string[]
```

`pdf_read` output contract:

```text
pdf_read(path_or_url, page_range?, purpose, max_chars?)
  -> source_id: string
     kind: pdf
     status: verified | blocked | failed | partial
     pages_total?: int
     pages_read: [int]
     text: string
     citations: [{ page: int, quote: string }]
     extracted_with: native_pdf_text | poppler_text | page_image_ocr | unavailable
     ledger_path: research/sources.jsonl
     warnings: string[]
```

`browser_read` output contract:

```text
browser_read(url, purpose, max_steps=3, max_chars?)
  -> source_id: string
     kind: rendered_page
     status: verified | blocked | failed | partial
     final_url: string
     title?: string
     rendered_text: string
     screenshot_artifact_path: string
     accessibility_snapshot_path?: string
     ledger_path: research/sources.jsonl
     reason_used: fetch_failed | js_required | table_rendering | visual_evidence
     warnings: string[]
```

Guardrails:

- `source_read` is preferred over direct `web_fetch` once implemented.
- `browser_read` is hard-only and requires prior failed/partial
  `source_read`/`pdf_read`, unless the user explicitly asks for rendered-page
  inspection.
- `browser_action` is not part of medium or normal hard research. It requires a
  separate profile flag and a per-run step budget.
- Browser tools use an isolated profile with no user cookies, no local secrets,
  no private network by default, no cloud metadata endpoints, and redaction
  before auxiliary summarization.
- Browser snapshots and screenshots are artifacts. The model should cite the
  source ledger row, not paste large browser snapshots into chat.
- PDF tools validate magic bytes/content type, size, page count when available,
  and return structured errors: `empty`, `too_large`, `password_protected`,
  `corrupted`, `scanned_no_text`, `unavailable`.
- Scanned PDFs without OCR/text extraction are not "verified text evidence"
  unless a vision/OCR path extracts citable text.

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
   - `verify`: `source_read` when implemented, otherwise `web_fetch`;
     `pdf_read`/`browser_read` for hard fallback; `read_file`; child joins
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
- `source_read` ledger guard: verified claims require a source ledger row with
  `status=verified` or an explicit caveat for `blocked/partial`.
- `pdf_read` citation guard: PDF-backed claims should carry page hints when the
  extractor provides page locations.
- `browser_read` fallback guard: browser reads require a recorded reason and a
  prior insufficient `source_read`/`pdf_read`, unless explicitly requested.
- `browser_action` approval guard: full browser interaction is disabled unless
  hard profile plus explicit browser-action flag are both set.
- `file_write` report guard: initial create is allowed; full rewrites after
  report exists require explicit controller reason.
- `file_edit/file_patch` stale-read guard: edit requires a fresh report read or
  preview after last report write.
- `agent_tool` scope guard: child prompts must be bounded, name subtopic,
  expected source count, language, and no final synthesis authority.
- final answer guard: medium/hard final answer cannot duplicate the report.

### Frontend Contract

Current UI truth on 2026-06-02:

- `ChatComposer` currently exposes web tools through a `Tools` popover and a
  separate binary `Deep` button.
- The binary `Deep` button toggles `researchDepth` between `standard` and
  `deep_parallel_research`. It does not expose `light`, `medium`, `hard`, cost
  expectations, artifact expectations, or source-verification expectations.
- `settingsStore` currently persists `toolPreset` and `researchDepth`.
  `toolPreset` is `off | web_search | web_fetch | web`; `researchDepth` is only
  `standard | deep_parallel_research`.
- `startChatStream` sends `tool_preset` and optional
  `research_depth=deep_parallel_research`; there is no typed
  `research_mode`, `research_profile`, `profile_source`, or hard-mode opt-in in
  the request contract.
- `ChatPage` shows an `Artifacts (N)` popover in the top-right session toolbar.
- `WorkspaceArtifactsPanel` can list session artifacts, prefer
  `research/report.md`, preview Markdown/text, show kind and size, and refresh
  manually.
- `DeepResearchPanel` can appear inside an assistant message and show verified
  count, candidate count, domain count, blocked count, report path/size, and
  the last few research progress events.
- `MessageList` hides duplicate `todo_write`/planning tool cards when the
  assistant bubble already contains a planning snapshot.
- Subagent lifecycle is visible through tool/delegated-work cards, but it is
  not summarized as one durable research timeline.

Architectural gaps found in the current UI/runtime contract:

- Deep Research state is currently attached to the latest assistant message,
  not to the run/session. That makes a persistent cockpit, reload recovery, and
  multi-run comparison fragile.
- The frontend reconstructs research state directly from SSE events. It does
  not have a typed canonical `DeepResearchViewState`.
- Replay can rebuild message bubbles from persisted events, but there is no
  dedicated state hydration endpoint for "what does this run look like now?"
- `/api/chat/runs/{run_id}/trace-summary` already exposes much of the needed
  truth, but frontend `RunTraceSummaryResponse` is typed only as
  `run_id/verdict/terminal_event/compaction`, so research diagnostics are
  effectively untyped in the UI.
- Phase exists in several places (`DeepResearchPhaseGateState`,
  `ResearchSessionContract`, trace summary), but there is no single UI-facing
  phase authority.
- Artifact lifecycle is binary-ish in the UI: "report exists" plus size/path.
  It does not distinguish `created`, `captured_inline`, `patched`, `edited`,
  `ready`, `stale`, or `failed_preview`.
- Source ledger counts are shown, but ledger rows are not promoted to a
  first-class UI model with verified/candidate/blocked/failed sections.
- Subagent state is displayed as tool card detail. There is no parent-level
  rollup of child task purpose, status, sources found, duplication, and compact
  findings.
- Raw/PDF download is planned, but path safety, size limits,
  `Content-Disposition`, conversion failure state, and UI fallback are not yet
  specified.
- Deep Research mode selection is under-specified. A binary `Deep` toggle cannot
  explain when the run will be a quick web lookup, artifact-first medium
  research, or high-cost hard research with auditors/PDF/browser fallbacks.
- Mode choice is not locked into run/session metadata in a UI-visible way. After
  the run starts, it is hard to tell whether behavior came from user selection,
  auto-routing, persisted settings, or backend classification.
- The current UI can allow confusing mental models: `Tools · Web` can be on
  while `Deep` is off, but `Deep` does not clearly state that it implies a
  different workflow and artifact contract, not merely "more web".
- Accessibility and responsive layout are not yet specified for the cockpit.

The current experience is technically useful but not yet a good research
cockpit: artifacts are discoverable only through a small popover, evidence
coverage is fragmented across cards, and the user cannot easily answer:
"what phase are we in, what evidence is verified, what report exists, and what
will I get at the end?"

Deep Research UI must make the process observable without forcing the user to
read raw tool logs:

- **Conversation lane**: concise status and final handoff only. Long prose must
  move to `research/report.md`.
- **Plan lane**: visible todo progress, current phase, blocked/pending steps,
  and explicit "ready for final" state.
- **Evidence lane**: verified sources, candidate-only sources, blocked reads,
  failed reads, distinct domains, and source ledger path.
- **Subagent lane**: child tasks grouped by purpose, status, source counts,
  compact child output previews, and duplication warnings.
- **Artifact lane**: current report path, size, last update time, update mode
  (`created`, `patched`, `edited`, `captured`), source ledger, claims file, raw
  Markdown download, and PDF export when available.
- **Health lane**: token spend, search/fetch counts, repeated search warnings,
  long-chat-before-report warnings, stale todo warnings, and Phoenix/trace
  summary link for debugging.

Recommended first-viewport layout:

- Keep chat as the primary surface.
- Promote Deep Research state to a persistent right-side drawer or top sticky
  compact bar when `deep_research_mode.enabled=true`.
- The compact bar should show phase, todo progress, verified/candidate/blocked
  counts, report status, and a single button to open the full research drawer.
- The drawer should have tabs: `Overview`, `Sources`, `Artifacts`, `Subagents`,
  `Trace`.
- Tool cards remain expandable details, not the primary way to understand the
  run.

#### Deep Research Mode Switching UX

Mode selection must be boringly explicit. The user should understand before
sending what kind of run will start, what it may cost, and what artifact they
will get.

Recommended control model:

- Replace the standalone binary `Deep` button with a compact `Mode` selector in
  the composer toolbar.
- Top-level modes:
  - `Chat`: no web/research unless manually enabled by a tool preset.
  - `Web`: quick search/fetch-backed answer, maps to `light`.
  - `Deep`: artifact-first research, opens a profile selector.
- Deep profiles:
  - `Medium` is the default selected Deep profile and should be labeled
    `Recommended`.
  - `Hard` is explicit opt-in and should show a compact warning about higher
    time/token cost and stricter verification.
  - `Light` should not be presented as "Deep"; it belongs to `Web` or `Quick
    research` because it has no report artifact or subagents.
- The active mode chip should be visible in the composer and in the run header:
  `Web`, `Deep: Medium`, or `Deep: Hard`.
- The selector must show the expected deliverable in one short line:
  - `Web`: short answer with links.
  - `Deep: Medium`: live `research/report.md` plus source ledger.
  - `Deep: Hard`: audited report, claim checks, broader source budget, optional
    PDF/browser-read fallback.
- Hard mode should require a deliberate click in the profile selector. It
  should not be silently selected by auto-routing.
- While a run is streaming, mode/profile controls are disabled. Changes made
  during an active run, if allowed later, apply only to the next user message
  and must be visually marked as queued for next run.
- Auto-routing may suggest a mode from the prompt, but it must be transparent:
  show `Suggested: Deep Medium` or `Suggested: Web` before send or in the run
  header after send. Auto-routing must never silently escalate to `Hard`.
- Persist the user's last selected mode/profile per browser, but also store the
  chosen values in run metadata so replay, screenshots, Phoenix, and scorecards
  can answer "what profile actually ran?"

Recommended request/settings contract:

```text
research_mode: chat | web | deep
research_profile: light | medium | hard | null
profile_source: user_selected | auto_suggested | backend_classified | scenario_forced
hard_options:
  allow_pdf_read: bool
  allow_browser_read: bool
  allow_browser_action: bool
```

Temporary compatibility mapping while backend is migrated:

- `research_mode=chat` -> `tool_preset=off`, no `research_depth`.
- `research_mode=web` -> `tool_preset=web`, no `research_depth`,
  trace profile `light`.
- `research_mode=deep`, `research_profile=medium` ->
  `tool_preset=deep_research`, `research_depth=deep_parallel_research`, trace
  profile `medium`.
- `research_mode=deep`, `research_profile=hard` ->
  `tool_preset=deep_research`, `research_depth=deep_parallel_research`, trace
  profile `hard`, hard fallback flags explicit.

The long-term backend API should stop overloading `research_depth` and accept
the typed mode/profile fields directly. `tool_preset` should describe
capability surface; `research_profile` should describe workflow, budgets,
artifact expectations, and guardrails.

User-facing states:

- `Starting`: workspace created, plan not yet written.
- `Planning`: todo/checklist is being created.
- `Discovering`: searches and/or subagents are finding candidates.
- `Verifying`: URLs are being fetched/read and classified.
- `Writing`: `research/report.md` is being created or patched.
- `Reviewing`: report/source coverage is being checked.
- `Ready`: report exists, source ledger exists, todos closed or reconciled.
- `Needs attention`: blocked fetches, stale todos, missing report, high token
  spend, repeated searches, or user approval needed.

Canonical UI state contract:

The frontend cockpit should render from one normalized object, not by scanning
message bubbles:

```text
DeepResearchViewState
  run_id: string
  session_id: string
  research_mode: chat | web | deep | unknown
  profile: light | medium | hard | unknown
  profile_source: user_selected | auto_suggested | backend_classified | scenario_forced | unknown
  phase: starting | planning | discovering | verifying | writing | reviewing | ready | needs_attention | failed | cancelled
  phase_source: contract | trace_summary | sse | inferred
  readiness: ready | blocked | needs_more_sources | needs_report | needs_review
  todos: { done: int, total: int, current?: string, stale: bool }
  artifacts:
    report?: { path, size_bytes, modified_at, lifecycle, preview_available, markdown_download_url?, pdf_download_url? }
    source_ledger?: { path, record_count, modified_at }
    claims?: { path, record_count, modified_at }
  sources:
    verified: SourceRow[]
    candidates: SourceRow[]
    blocked: SourceRow[]
    failed: SourceRow[]
    distinct_domains: int
  subagents:
    groups: SubagentGroupView[]
    total_children: int
    running_children: int
    completed_children: int
    failed_children: int
    duplicated_queries: int
  metrics:
    prompt_tokens?: int
    completion_tokens?: int
    total_tokens?: int
    web_search_count: int
    web_fetch_count: int
    report_full_write_count: int
    report_patch_count: int
    long_chat_before_report_chars: int
  warnings: WarningView[]
  trace:
    run_id: string
    verdict?: pass | warn | fail
    failure_flags: string[]
    phoenix_url?: string
```

State authority order:

1. Live SSE events update the in-memory view state during an active run.
2. A backend view-state projection endpoint rebuilds the same shape from
   replay events, trace summary, session metadata, and workspace artifacts.
3. Frontend reload/hydration uses the projection endpoint first, then resumes
   SSE if the run is still active.
4. Message-level `DeepResearchPanel` may remain as a compact inline summary,
   but it must be derived from the run-level view state.

Recommended backend endpoint:

- `GET /api/chat/runs/{run_id}/deep-research-state`.
- Response shape is `DeepResearchViewState`.
- It should be generated from stored stream events plus workspace artifact
  inspection, not from frontend-only state.
- It should include typed subsets of trace summary fields needed by UI.

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

UX acceptance metrics:

- A user can choose `Web`, `Deep: Medium`, or `Deep: Hard` before sending and
  can see the chosen mode in the run after sending.
- `Medium` is the default Deep profile, but a fresh ordinary chat does not
  silently run medium Deep Research unless the user selects Deep or accepts an
  explicit suggestion.
- `Hard` cannot be selected by hidden auto-routing and cannot start without a
  visible opt-in state in the UI and run metadata.
- A user can tell the current Deep Research phase within 3 seconds without
  opening raw tool cards.
- A user can open `research/report.md` from the research UI while the run is
  still active.
- A user can distinguish verified sources from search candidates.
- A user can see whether subagents are running, completed, failed, or redundant.
- A user can see why the agent is not finished yet.
- A user can download Markdown/PDF once the report exists.
- Playwright screenshots show no long report prose above a `0/N` plan state.

## Implementation Phases

### Phase 0: Baseline Reproduction And Instrumentation

Goal: make the current failure impossible to hand-wave.

Live Deep Research tests are valid only when all observability channels are
enabled together:

- durable runtime store: `AGENT_DRIVER_RUNTIME_STORE_KIND=sqlite` or `jsonl`;
  for SQLite use `AGENT_DRIVER_SQLITE_PATH`, not a non-existent
  `AGENT_DRIVER_RUNTIME_STORE_SQLITE_PATH`;
- run-scoped sessions/workspace paths under the artifact directory;
- Phoenix tracing: `CHAT_DEMO_TRACING_ENABLED=true`,
  `PHOENIX_COLLECTOR_ENDPOINT=...`;
- backend/frontend/live-probe logs captured to the same artifact directory;
- Playwright screenshots and transcript excerpts;
- `/api/chat/runs/{run_id}/trace-summary` and workspace artifact snapshots.

If any of these are missing, the run is a harness failure, not a Deep Research
baseline. Do not make architecture decisions from a run that used the in-memory
runtime store or had Phoenix disabled.

Before a Deep Research live run, run one short live model/tool preflight
scenario, for example `model-preflight-search-fetch`. This verifies provider
configuration, SSE streaming, tool execution, trace summary, Phoenix export,
and durable event persistence without the entropy of Deep Research. The
preflight may be skipped only when a recorded fingerprint of the relevant
provider/runtime/tool/SSE/probe code is unchanged since the last passing
preflight.

Checklist:

- [x] Run model/tool preflight or record that the code fingerprint is unchanged
  from the last passing preflight.
- [x] Confirm `/api/health` reports a non-memory runtime store.
- [x] Confirm `/api/health` reports Phoenix tracing enabled.
- [x] Run the current fork-join prompt in the chat demo with live model.
- [x] Save Playwright full-page screenshot.
- [x] Save `/api/chat/runs/{run_id}/trace-summary`.
- [ ] Save Phoenix trace link or exported trace data.
- [x] Save backend logs around run start/end and tool calls.
- [x] Confirm whether `deep_research_artifact_expected` is true.
- [x] Confirm tool chain contains or lacks `file_write`, `read_file`,
  `file_patch`, `agent_tool`.
- [x] Confirm visible plan completion state at final.
- [x] Record token usage, cost, search/fetch counts, artifact paths.

Gate:

- Do not change architecture until one current failure and one current pass are
  captured with screenshots and scorecards.
- A Deep Research run without durable DB, Phoenix, logs, screenshots,
  trace-summary, and workspace artifacts does not satisfy Phase 0.

Execution note 2026-06-02:

- Added `scripts/run_deep_research_live_observed.sh` as the canonical live
  runner for this phase. It enables SQLite, Phoenix, run-scoped sessions,
  run-scoped workspace, Playwright artifacts, and a combined `live-run.log`.
- The runner performs a model/tool preflight (`model-preflight-search-fetch`)
  before Deep Research unless the fingerprint of relevant provider/runtime/tool
  and SSE/probe files matches the last passing preflight. Use
  `DEEP_RESEARCH_FORCE_PREFLIGHT=1` to force the preflight.
- Fixed observed-run setup issues found during Phase 0:
  - Phoenix Python dependency was missing from the active `.venv`; installing
    the backend package enabled `phoenix.otel`.
  - Phoenix collector/UI must be running at `http://127.0.0.1:6006`; Docker
    compose service `phoenix` was started and accepted OTLP posts.
  - The runtime SQLite env var is `AGENT_DRIVER_SQLITE_PATH`.
  - Live probe must not reuse the latest old session when response headers are
    unavailable; it now snapshots known run ids before send and waits for a new
    persisted run.
  - Live matrix/profile payloads now use valid `profile_source=scenario_forced`.
- Observed preflight + light run:
  - command: `DEEP_RESEARCH_PROFILES=light
    DEEP_RESEARCH_QUESTION_ID=fork-join-canary
    DEEP_RESEARCH_FORCE_PREFLIGHT=1
    CHAT_DEMO_LIVE_ARTIFACT_DIR=/tmp/chat-demo-live-observed-light-20260602
    scripts/run_deep_research_live_observed.sh`;
  - preflight passed: `run_d7d45bdb25a5`, tools `web_search -> web_fetch`,
    7,505 tokens;
  - light fork-join failed despite expected answer present:
    `run_c609f547c5d6`, 21,563 tokens,
    `skill_tool -> web_search -> web_search -> web_fetch...`,
    failure `insufficient_research_source_diversity`;
  - health confirmed `store_kind=sqlite`, `tracing.enabled=true`,
    provider healthy.
- Observed medium run:
  - command: `DEEP_RESEARCH_PROFILES=medium
    DEEP_RESEARCH_QUESTION_ID=fork-join-canary
    CHAT_DEMO_LIVE_ARTIFACT_DIR=/tmp/chat-demo-live-observed-medium-20260602
    scripts/run_deep_research_live_observed.sh`;
  - preflight was skipped because the fingerprint matched the last passing
    preflight;
  - medium fork-join failed: `run_db249d1295d8`, terminal `run_failed`,
    102,572 tokens, 3 child runs completed;
  - workspace snapshot did contain `research/report.md` and
    `research/sources.jsonl`, but trace-summary/scorecard/UI treated the report
    as missing because no recognized `research/report.md` artifact update or
    `file_write` report event was recorded;
  - failures: `run_failed_or_cancelled`, `unknown_tool_call`,
    `deep_research_no_report_artifact`, `deep_research_missing_initial_todo`,
    `deep_research_skill_denied`, `deep_research_phase_violation`;
  - phase violations: 12, first tool was `skill_tool` while phase gate expected
    `todo_write`;
  - tool chain included repeated `read_file`/artifact calls and both
    `artifact_list` and unexpected `artifacts_list`, which should be treated as
    a capability/prompt mismatch.
- Phoenix evidence: container logs show repeated `POST /v1/traces 200 OK` while
  the observed runs executed. Manual review in Phoenix UI remains required for
  per-span interpretation.
- Phoenix API note: the local Phoenix UI responded on 2026-06-02 with
  `platformVersion=13.20.0`, `/healthz=OK`, and GraphQL fields including
  `projects`, `getTraceByOtelId`, and `getSpanByOtelId`. Phase 0.5 should add a
  small exporter instead of relying on manual UI screenshots.
- Implementation note 2026-06-02:
  - Added `scripts/export_phoenix_evidence.py`, which exports Phoenix GraphQL
    project counters and selected project evidence to `phoenix-evidence.json`.
  - `scripts/run_deep_research_live_observed.sh` now writes Phoenix evidence
    after the live run even when the matrix fails, then returns the original run
    exit code.
  - Phoenix health status now distinguishes `configured` from `enabled` and
    includes project/endpoint metadata.
  - Trace summaries now expose `report_trace_update_seen` and
    `report_write_seen`.
  - Live scorecards now print `report_projection` fields and classify
    workspace-vs-trace report disagreements as projection mismatches.
  - Frontend artifact panel now invalidates workspace artifacts on
    `artifact_created`/`artifact_updated` SSE events and can show known report
    artifacts from run-level `DeepResearchViewState` while the workspace query
    is stale.
  - Live probe now stops doomed runs early on unknown tool calls, excessive
    phase violations, or token-budget runaway before report projection.
  - Repository scan found no `artifacts_list` prompt/repair path outside
    observed-run documentation and the regression test for unknown-tool
    detection; canonical tool names remain `artifact_list`, `artifact_read`,
    and `artifact_preview`.
  - Curated research skill reminder now includes `trusted_roots` alongside
    `base_dir`, so bundled skills can be discovered/viewed without workspace
    jail denial.
  - Deep Research phase allowed tools were relaxed to permit skill bootstrap
    before todo, todo progress updates after planning, and artifact preview/read
    during write/review.

Interpretation:

- The preflight pass means provider, basic web tools, SSE, durable store, and
  Phoenix ingestion can work together.
- The light failure is expected for the current harness but not acceptable:
  the profile still spent 21k tokens, loaded `skill_tool`, wrote a long answer,
  and failed source diversity. That means light needs its own prompt/budget and
  profile-specific score thresholds instead of inheriting Deep Research
  expectations.
- The medium failure is not a research-quality result yet. It is a contract
  failure: unknown tool vocabulary, phase gate/tool-order mismatch, report
  artifact projection mismatch, and token-budget runaway happened before we can
  fairly judge the research architecture.
- The workspace-vs-trace disagreement is a blocker for benchmark decisions. A
  run where `research/report.md` exists in the workspace but the scorecard says
  it is missing must be classified as instrumentation/controller failure until
  the projection is fixed.
- Do not compare architecture variants yet. First build one contract-correct
  medium baseline that can create the report, show it in UI, record it in
  trace-summary, and stop before wasting 100k tokens on a doomed run.

Observed rerun note 2026-06-02:

- Phase 0.5 light canary passed with SQLite, Phoenix, Playwright, logs, and
  workspace snapshots enabled:
  - command: `DEEP_RESEARCH_PROFILES=light
    DEEP_RESEARCH_QUESTION_ID=fork-join-canary
    DEEP_RESEARCH_FORCE_PREFLIGHT=1
    CHAT_DEMO_LIVE_ARTIFACT_DIR=/tmp/chat-demo-live-observed-light-phase05-20260602
    scripts/run_deep_research_live_observed.sh`;
  - preflight passed with `web_search -> web_fetch`;
  - light run passed, terminal `run_completed`, no report artifact expected,
    no failures, 22,625 tokens, chain
    `skill_tool -> web_search -> web_search -> web_search -> web_fetch...`;
  - Phoenix evidence was exported to `phoenix-evidence.json`.
- The light result is contract-correct but still inefficient. It should not
  load Deep Research skill by default, and its score should reward compact
  source-backed answers instead of broad Deep Research behavior.
- Phase 0.5 medium rerun was manually stopped after it exceeded the useful
  observation window:
  - command: `DEEP_RESEARCH_PROFILES=medium
    DEEP_RESEARCH_QUESTION_ID=fork-join-canary
    CHAT_DEMO_LIVE_ARTIFACT_DIR=/tmp/chat-demo-live-observed-medium-phase05-20260602
    scripts/run_deep_research_live_observed.sh`;
  - workspace contained `research/report.md` and `research/sources.jsonl`;
  - parent trace still did not see parent-owned report/source-ledger creation;
  - partial parent chain was
    `skill_tool -> todo_write -> skill_view -> glob_search -> glob_search ->
    todo_write -> agent_tool -> agent_tool -> agent_tool`;
  - parent started another child wave after earlier children completed;
  - partial failures remained `missing_terminal_event`,
    `missing_required_research_evidence`, `subagent_no_final`,
    `deep_research_no_report_artifact`,
    `deep_research_no_source_ledger_artifact`,
    `deep_research_missing_initial_todo`, and `deep_research_skill_denied`.
- Interpretation: the medium blocker is no longer only unknown tool names. The
  controller must enforce parent-owned artifacts and bounded child fan-out.
  Children may gather compact source notes, but the parent must create/update
  `research/report.md` and `research/sources.jsonl` in its own run trace.
- Follow-up medium rerun after bounded-child fixes:
  - command: `DEEP_RESEARCH_PROFILES=medium
    DEEP_RESEARCH_QUESTION_ID=fork-join-canary
    DEEP_RESEARCH_LIMIT=1
    CHAT_DEMO_LIVE_ARTIFACT_DIR=/tmp/chat-demo-live-observed-medium-phase05b-20260602
    scripts/run_deep_research_live_observed.sh`;
  - preflight passed again because the code fingerprint changed;
  - result still failed, but differently: no unknown tools, no unbounded child
    wave, `runs_started=2`, `runs_completed=2`, `groups_joined=1`;
  - total tokens: 66,944, which is still too high for a failed medium canary;
  - workspace contained `research/report.md` and `research/sources.jsonl`, but
    parent trace still had no recognized `file_write`/artifact update and no
    parent-owned source ledger;
  - `research/report.md` was a child long-final auto-capture, not a real parent
    synthesis. It contained “ready-to-save Markdown” and `Fetched/Read: No`
    entries, so it must not count as a successful medium report;
  - child prompts still contained stale “Write a summary to research/...”
    instructions, causing a denied `write_file` attempt and another LLM retry;
  - UI still showed `Artifacts (0)` / report not started while workspace
    preview could read the file, confirming the projection/ownership problem.
- Follow-up medium rerun after disabling child artifact capture:
  - command: `DEEP_RESEARCH_PROFILES=medium
    DEEP_RESEARCH_QUESTION_ID=fork-join-canary
    DEEP_RESEARCH_LIMIT=1
    CHAT_DEMO_LIVE_ARTIFACT_DIR=/tmp/chat-demo-live-observed-medium-phase05c-20260602
    scripts/run_deep_research_live_observed.sh`;
  - run was stopped manually after it exposed the next contract bug;
  - child no longer created workspace files or attempted `write_file`;
  - child still inherited parent Deep Research repair metadata, so the runtime
    tried to force child `web_fetch`/`todo_write` repair loops even though the
    child worker surface intentionally excludes `todo_write`;
  - fix: `deep_research_child_notes_only` children now strip parent
    `deep_research_mode`, `deep_research_phase_gate`, and parent
    `task_contract` from the child tool policy. Children keep only their worker
    role/tool surface and return source notes to the parent.

### Phase 0.5: Observed Contract Triage Before Architecture Comparison

Goal: repair the mismatches exposed by the observed runs before more expensive
Deep Research benchmarking.

Checklist:

- [x] Export or link at least one Phoenix trace for the observed preflight,
  light, and medium runs. Container `POST /v1/traces 200 OK` is necessary but
  not enough.
- [x] Fix Phoenix status normalization so health, scorecards, and trace-summary
  agree on `enabled`, `configured`, endpoint, and last-export evidence.
- [x] Fix report artifact detection:
  - workspace `research/report.md` existence counts as report existence;
  - trace artifact updates, `file_write`, `file_edit`, `file_patch`, and
    artifact preview/read events are reconciled by path;
  - scorecard reports `workspace_exists`, `trace_update_seen`,
    `trace_path_seen`, and `report_write_seen` separately.
- [x] Fix UI artifact projection when the workspace has report artifacts but
  the run header still shows `Artifacts (0)`.
- [x] Canonicalize artifact tool vocabulary:
  - supported names are `artifact_list`, `artifact_read`, `artifact_preview`;
  - prompts, skills, tests, and error-repair hints must not suggest
    `artifacts_list`;
  - unknown artifact tool calls should trigger immediate repair/abort, not
    another long LLM loop.
- [x] Revisit phase gate strictness:
  - decide whether `skill_tool` is allowed before the first `todo_write`;
  - allow `todo_write` progress updates outside the initial plan phase;
  - allow verification tools in the real phase where they are required;
  - start with warn-and-repair mode, then move to hard-deny only after the
    observed medium canary passes.
- [x] Add early abort thresholds:
  - abort/repair after unknown tool call;
  - abort after more than N phase violations;
  - abort if medium exceeds the configured token budget before a recognized
    report artifact exists;
  - abort if medium/hard starts more child runs than the profile budget allows;
  - abort if the report exists in workspace but cannot be projected into
    trace-summary/UI.
- [ ] Split light scoring from medium/hard scoring:
  - light may require one fetched source/domain and a short answer;
  - light must not require report artifacts or subagents;
  - light should not load Deep Research skill unless explicitly requested.
- [x] Run the preflight again only if the fingerprint changed; otherwise reuse
  the recorded passing fingerprint.
- [x] Re-run one light canary with SQLite, Phoenix, logs, screenshots,
  trace-summary, and workspace artifacts enabled.
- [ ] Re-run one medium fork-join canary with SQLite, Phoenix, logs,
  screenshots, trace-summary, and workspace artifacts enabled after the
  parent-owned artifact/subagent fan-out fixes.
- [x] Enforce Deep Research child ownership defaults:
  - medium children default to `researcher` worker surface;
  - children do not inherit `file_write`, `file_edit`, `file_patch`, or
    `agent_tool` unless a later hard profile explicitly opts in;
  - child prompts say to return compact source notes only.
- [x] Disable Deep Research artifact auto-capture and source-ledger persistence
  for child runs. Child final answers must not become parent
  `research/report.md`.
- [x] Sanitize child prompts so stale instructions like “write a summary to
  research/*.md” are rewritten into “return notes to the parent”.
- [x] Strip parent Deep Research contract metadata from child notes workers so
  they do not enter parent repair loops or try `todo_write`.
- [x] Enforce bounded medium child fan-out:
  - light max child requests: 0;
  - medium max child requests: 2;
  - hard max child requests: 4;
  - extra `agent_tool` requests are recorded as subagent backpressure instead
    of opening another child wave.
- [x] Add matching live-probe early stop for subagent fan-out so profile
  runaway is cancelled before another expensive child wave.
- [ ] Ensure the parent creates or patches `research/report.md` and
  `research/sources.jsonl` after child join, even when children return useful
  notes.
  - [x] Record a run-level `deep_research_child_synthesis` handoff when a
    Deep Research child group joins.
  - [x] Move the Deep Research phase contract to `write` while child notes are
    pending and no parent-owned report exists.
  - [x] Add a runtime attachment that tells the parent not to write long prose
    or spawn another child wave before writing/patching parent artifacts.
  - [x] Emit a trace-visible `research_progress` marker and expose
    `subagents.child_synthesis_pending` in trace-summary for live scorecards.
  - [x] Expose `first_tool_after_child_synthesis_pending` and
    `unexpected_tool_after_child_synthesis_pending` in trace-summary, and make
    the live probe stop medium runs that start another search/delegation path
    after child join before parent artifact projection.
  - [x] Add contract-repair reason `child_synthesis_pending` and force the
    next repair turn toward parent `file_write` before falling back to
    report preview/patch tools.
  - [ ] Enforce or repair the actual parent `file_write`/`file_patch` call and
    verify it in a live medium trace.
- [ ] Make parent trace-summary optionally show child evidence separately
  (`child_search_count`, `child_fetch_count`, `child_verified_read_count`) while
  keeping medium readiness gated on parent-owned synthesis.

Gate:

- Do not begin architecture-variant comparison until the medium canary fails
  only for research quality/source coverage, not for unknown tools, missing
  report projection, phase-policy mismatch, or absent Phoenix evidence.

### Phase 1: Capability Surface Fix

Goal: make the tool surface honest.

Checklist:

- [ ] Split presets or profile metadata:
  - `research_light`
  - `deep_research_medium`
  - `deep_research_hard`
- [x] Enable subagents for medium/hard Deep Research.
- [x] Include `agent_tool` in medium/hard tool surface.
- [ ] Keep `bash` off for medium; keep `python` off unless hard allows it.
- [x] Update `deep-research-report/SKILL.md` to list real tools:
  `web_search`, `web_fetch`, `todo_write`, `agent_tool`, `read_file`,
  `file_write`, `file_edit`, `file_patch`, `artifact_preview`,
  `artifact_read`, `artifact_list`.
- [ ] Add profile-specific skill sections:
  - light: source-backed short answer;
  - medium: artifact-first report plus bounded subagents;
  - hard: verifier/auditor and export.
- [ ] Add a prompt/contract check that rejects non-existent tool names in skill
  metadata during startup or tests.
- [x] Add profile-level `max_subagent_requests` to the Deep Research task
  contract and runtime reminders.
- [x] Default Deep Research `agent_tool` children to the `researcher` worker
  surface so they can search/fetch/read but cannot write parent artifacts.

Execution note 2026-06-02:

- `deep_research` now exposes `agent_tool` and enables subagents.
- The phase gate, research contract, and trace summary allow `agent_tool` during
  discovery.
- Skill metadata tests now reject the stale `run_subagent` name for the bundled
  Deep Research skill.
- Medium `agent_tool` planning now applies a total child-request cap and records
  skipped requests in `subagent_backpressure`.
- Dedicated `research_light` / `deep_research_medium` /
  `deep_research_hard` presets are still pending.

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
- [x] Add a trace field:
  `first_report_update_before_long_chat: bool`.
- [x] Add a trace field:
  `long_chat_before_report_chars: int`.
- [x] Add a trace field:
  `report_full_write_count` and `report_patch_count`.

Execution note 2026-06-02:

- Default inline Deep Research draft capture threshold is now 1,800 chars.
- Trace summary now reports pre-report chat length, whether the report appeared
  before long chat prose, and report patch/edit count.
- A separate metadata/tool event for draft capture is still pending, so the
  threshold checklist item remains open.

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

### Phase 3.5: User Observation UX

Goal: make live Deep Research understandable to a user while it is running.

Checklist:

- [x] Replace the binary composer `Deep` toggle with an explicit mode/profile
  selector:
  - top-level `Chat`, `Web`, `Deep`;
  - Deep profile selector with `Medium (Recommended)` and `Hard`;
  - concise deliverable/cost hints for each choice.
- [x] Add typed frontend settings:
  - `researchMode: chat | web | deep`;
  - `researchProfile: light | medium | hard | null`;
  - `profileSource: user_selected | auto_suggested | backend_classified |
    scenario_forced`.
- [x] Add temporary compatibility mapping from the new frontend settings to the
  existing backend fields until the backend accepts typed mode/profile directly.
- [x] Add backend request/run metadata for selected mode/profile so replay,
  trace summary, Phoenix, and scorecards can show what actually ran.
- [x] Lock mode/profile at run start. Disable switching while streaming, or mark
  changes as applying to the next run only.
- [x] Require visible hard-mode opt-in. Auto-routing may suggest `Hard` only as
  a user-confirmed choice; it must never silently start hard research.
- [ ] Show the active mode/profile chip in the composer, run header, compact
  research bar, and final handoff.
- [ ] Add Playwright request/DOM assertions:
  - `Web` sends light-compatible settings and shows no report requirement;
  - `Deep: Medium` sends medium profile metadata and expects report artifact;
  - `Deep: Hard` requires explicit selection and sends hard flags;
  - controls are disabled or next-run-marked during streaming;
  - reload preserves the run's chosen mode/profile in the cockpit.

Execution note 2026-06-02:

- The composer now uses an explicit mode selector instead of the binary `Deep`
  toggle.
- Frontend settings now persist `researchMode`, `researchProfile`,
  `profileSource`, and hard fallback options.
- `startChatStream` sends `research_mode`, `research_profile`,
  `profile_source`, `hard_options`, and compatibility
  `research_depth=deep_parallel_research`.
- Deep mode sends `tool_preset=deep_research`; legacy `web_search` /
  `web_fetch` presets are not widened by backend compatibility code.
- Backend request/run metadata stores selected mode/profile and propagates it
  into `app_metadata` and the Deep Research task contract.
- Session/replay metadata parsing now preserves `research_mode`,
  `research_profile`, `profile_source`, `hard_options`, and
  `research_depth`.
- Assistant messages can show a compact mode/profile chip from run metadata,
  and the message metadata popover includes research mode/profile details.
- Manual Playwright checks verified the selector and payload. Screenshot:
  `/tmp/chat-demo-mode-selector.png`.
- Remaining UI work: run header/cockpit/final-handoff chips, frontend
  run-level state, reload hydration, and committed Playwright e2e assertions.

Execution note 2026-06-02:

- Added `GET /api/chat/runs/{run_id}/deep-research-state`.
- Added typed backend `DeepResearchViewState` response with mode/profile,
  phase, readiness, todo progress, artifacts, source counts, subagent summary,
  token/tool metrics, warnings, and trace subset.
- The projection is built from stored stream events, trace summary, session
  metadata, and workspace artifact inspection.
- Added typed frontend `DeepResearchViewState` API models and
  `fetchDeepResearchState(runId)`.
- Added backend endpoint coverage through the ASGI test client.
- Remaining UI work: frontend reload/SSE hydration edge cases, full research
  drawer, source row sections, subagent rollup cards, and committed Playwright
  checks against the live UI.

Execution note 2026-06-02:

- Added a run-level `deepResearchView` slice to the chat store.
- `ChatPage` now fetches `DeepResearchViewState` for the latest run and stores
  it separately from message-level diagnostics.
- Terminal run handling invalidates the deep-research-state query so completed
  runs can refresh the canonical projection.
- Added a compact Deep Research status bar showing profile, phase/todos, source
  counts, report status, and readiness warning.
- Remaining UI work: active SSE merge into the same state, full drawer/tabs,
  source row sections, subagent rollup cards, and committed Playwright
  screenshot checks.
- [x] Define one normalized run-level state object for Deep Research UI:
  - phase;
  - todo progress;
  - report artifact status;
  - source ledger counts;
  - subagent group summaries;
  - warning/health flags;
  - trace-summary/run ids.
- [x] Add backend projection endpoint:
  `GET /api/chat/runs/{run_id}/deep-research-state`.
- [x] Build the projection from stored stream events, trace summary, session
  metadata, and workspace artifact inspection.
- [x] Add typed frontend API/schema for `DeepResearchViewState`; do not leave
  research diagnostics as `Record<string, unknown>`.
- [x] Add a run-level store slice for Deep Research view state. Message-level
  panels should consume a derived compact summary, not own the canonical state.
- [ ] Add reload/hydration behavior:
  - opening an existing session fetches replay plus deep research state;
  - active runs merge SSE updates into the same state;
  - completed runs show the last projected state without needing SSE.
- [ ] Define artifact lifecycle states:
  - `not_started`;
  - `created`;
  - `captured_inline`;
  - `patched`;
  - `edited`;
  - `ready`;
  - `stale`;
  - `preview_failed`;
  - `export_failed`.
- [ ] Promote source ledger rows into UI sections:
  - verified;
  - candidate;
  - blocked;
  - failed;
  - assistant links.
- [ ] Add parent-level subagent rollups:
  - purpose;
  - status;
  - child counts;
  - sources found;
  - tools used;
  - compact findings;
  - duplication warnings.
- [ ] Promote Deep Research state from per-message diagnostics to a persistent
  research cockpit when a deep run is active.
- [ ] Add compact sticky status bar:
  - current phase;
  - `N/M` todos done;
  - verified/candidate/blocked counts;
  - report state (`not started`, `draft`, `patched`, `ready`);
  - token count if available.
- [ ] Add full research drawer with tabs:
  - `Overview`: phase timeline, todo state, readiness, warnings;
  - `Sources`: verified/candidate/blocked/failed URLs and domains;
  - `Artifacts`: report/source ledger/claims previews and downloads;
  - `Subagents`: child task cards, status, tools used, compact outputs;
  - `Trace`: run id, Phoenix link/export placeholder, trace-summary flags.
- [ ] Make artifact access first-class:
  - report preview can open from the compact bar and from final message;
  - report is visible while run is active, not only after completion;
  - refresh/invalidation happens on `artifact_created`/`artifact_updated`
    events.
- [ ] Add human-readable warnings:
  - long chat before report;
  - plan stale or `0/N` after report text appears;
  - report missing;
  - source ledger missing;
  - too many repeated searches;
  - subagent duplication;
  - final answer too long after report exists.
- [ ] Add empty/loading/error states:
  - "workspace created, waiting for first artifact";
  - "report exists but preview failed";
  - "PDF export unavailable";
  - "source verification blocked by fetch errors".
- [ ] Add download/PDF UX safety:
  - server validates artifact path under session workspace;
  - response uses safe filename and `Content-Disposition`;
  - large files have size limits and clear UI errors;
  - Markdown download works even when PDF conversion fails;
  - PDF conversion failures are surfaced as `export_failed`, not hidden.
- [ ] Add accessibility and responsive behavior:
  - keyboard navigation through compact bar/drawer/tabs;
  - aria-live updates for phase changes;
  - accessible labels for source status and warnings;
  - mobile layout uses a full-height bottom sheet or full-screen drawer;
  - chat text and cockpit controls do not overlap.
- [ ] Add Playwright checks that inspect screenshots and DOM state:
  - compact bar visible for deep runs;
  - phase changes from planning/discovering to writing/reviewing/ready;
  - artifact preview opens during active run;
  - source counts match trace summary;
  - subagent cards roll up into the cockpit;
  - reload preserves cockpit state for a completed run;
  - Markdown download appears when report exists and PDF failure state is
    visible when export fails;
  - no long assistant report is visible while todo progress remains `0/N`.

Live check:

```bash
CHAT_DEMO_URL=http://localhost:5174 \
  .venv/bin/python scripts/deep_research_live_matrix.py \
  --profiles medium \
  --question-id fork-join-canary \
  --limit 1 \
  --screenshots
```

Gate:

- In a Playwright screenshot, the user can see phase, todo progress, source
  counts, report status, and active artifacts without expanding raw tool cards.
- The research cockpit updates during SSE streaming.
- Final answer references the report and the UI offers report preview/download
  from the same session.

### Phase 4: Hard Mode, Citation Audit, And Export

Goal: create a high-confidence mode that justifies extra token spend.

Checklist:

- [ ] Wire hard profile metadata into the Phase 3.5 selector and backend
  request contract.
- [ ] Add verifier/auditor subagent role.
- [ ] Add `source_read` as the preferred source evidence tool:
  - accepts URL or source/search-result ID;
  - writes/updates `research/sources.jsonl`;
  - returns status, source kind, excerpt, content hash, and artifact path;
  - internally uses existing `web_fetch` first.
- [ ] Add `pdf_read` for hard profile:
  - validates PDF magic bytes/content type and size before model exposure;
  - supports page ranges;
  - returns structured PDF errors;
  - records page-aware citation hints where possible.
- [ ] Add `browser_read` as hard-only fallback:
  - read-only rendered DOM/text/screenshot;
  - isolated browser profile;
  - no cookies/secrets/private network by default;
  - SSRF and cloud metadata endpoint blocks;
  - screenshot/snapshot artifact capture.
- [ ] Keep full browser automation (`browser_action`) out of default hard mode
  until there is a separate browser-action safety gate and live eval.
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
- [ ] Add Playwright hard fallback scenarios:
  - PDF source with extractable text and page citation hints;
  - fetch-blocked/JS-heavy page that requires `browser_read`;
  - browser safety canary that must block private/metadata URLs;
  - scanned PDF or unreadable PDF that must become `partial/failed`, not
    verified evidence.

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
- Hard run prefers `source_read` over raw browser usage when the tool exists.
- Browser fallback is observable in trace summary, source ledger, artifacts, and
  screenshot output.

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
  - requested research mode/profile;
  - profile source (`user_selected`, `auto_suggested`, `scenario_forced`,
    `backend_classified`);
  - tool chain;
  - search/fetch/domain counts;
  - child count;
  - report update pattern;
  - answer correctness;
  - failure flags.
- [ ] Compare profiles on the same questions.

Benchmark quality gaps found on 2026-06-02:

- `scripts/deep_research_live_matrix.py` still treats matrix success mostly as
  `expected_answer_regex` found in transcript/workspace preview. That is useful
  as one correctness signal, but it is not enough for Deep Research.
- The current trace probe checks many runtime risks, but the matrix summary does
  not expose a full acceptance breakdown. A run can look green in the matrix
  while still being hard to debug or compare.
- The public FRAMES cases are good exact-answer canaries, but they are not
  report-quality tasks. They do not prove that the agent writes an artifact
  first, keeps the chat concise, verifies claims, or produces a useful source
  ledger.
- Screenshots are currently mostly final-state evidence. We also need milestone
  screenshots during the run: after plan creation, after first search/fetch,
  after first report write, after a patch/edit, and at final state.
- The benchmark does not yet measure reproducibility. One lucky run should not
  certify medium/hard profiles.
- The benchmark does not yet separate model failures from infra/provider/UI
  failures. This makes regressions look noisy and slows root-cause analysis.
- Source URLs in the manifest are evaluator-only data. They must not be injected
  into model prompts, otherwise the benchmark becomes a leakage test.

Deep Research benchmark must be multi-axis. A run passes only when all required
axes for its profile pass:

- Answer correctness:
  - exact-answer tasks match `expected_answer_regex`;
  - report-style tasks satisfy a rubric with required claims, caveats, and
    domain-specific coverage.
- Evidence correctness:
  - final claims cite fetched URLs, not only search snippets;
  - report URLs appear in the source ledger;
  - blocked or paywalled sources are marked as blocked/preliminary instead of
    being presented as verified;
  - evaluator-only `source_urls` are used only by the checker.
- Process correctness:
  - first Deep Research action is planning/todo;
  - medium/hard use bounded subagents when required;
  - parent owns final synthesis;
  - report is created early and patched/edited, not rewritten as a long chat
    message;
  - phase-gate violations and todo/final mismatch block the run.
- UX correctness:
  - selected mode/profile is visible before send and in the run header;
  - hard profile cannot start without visible opt-in;
  - user can see current phase, todo progress, source status, subagent status,
    artifact status, and run health from the chat UI;
  - artifact preview works during and after the run;
  - reload/hydration restores the Deep Research view state;
  - screenshots prove there is no long answer prose above a `0/N` or incomplete
    plan state.
- Efficiency:
  - tokens, wall-clock, search/fetch counts, repeated queries, repeated reads,
    child count, and output tokens after first report update are under the
    scenario budget;
  - light/medium/hard are compared on the same questions instead of only checked
    independently.
- Reliability:
  - medium fork-join canary should pass repeated runs before being trusted;
  - hard profile can have a higher cost budget, but failures must classify as
    `model_behavior`, `tool_surface`, `provider`, `backend`, `frontend`,
    `phoenix`, or `benchmark_assertion`.

Planned manifest extensions:

```json
{
  "id": "fork-join-canary",
  "profile_expectations": {
    "light": {
      "max_total_tokens": 12000,
      "max_wall_clock_seconds": 360,
      "min_verified_sources": 1,
      "requires_report": false,
      "requires_subagents": false
    },
    "medium": {
      "max_total_tokens": 45000,
      "max_wall_clock_seconds": 720,
      "min_verified_sources": 3,
      "min_domains": 2,
      "requires_report": true,
      "requires_subagents": true
    },
    "hard": {
      "max_total_tokens": 90000,
      "max_wall_clock_seconds": 1200,
      "min_verified_sources": 6,
      "min_domains": 3,
      "requires_report": true,
      "requires_subagents": true,
      "requires_claim_audit": true,
      "prefers_source_read": true,
      "allows_pdf_read": true,
      "allows_browser_read_fallback": true,
      "allows_browser_action": false
    }
  },
  "required_claims": [
    "defines fork-join queueing model",
    "explains why exact analysis is difficult",
    "connects model to parallel/distributed/networked systems"
  ],
  "ui_expectations": {
    "requires_artifact_preview": true,
    "requires_source_ledger_panel": true,
    "requires_reload_hydration": true
  },
  "source_expectations": {
    "requires_pdf_read": false,
    "requires_browser_read_fallback": false,
    "forbids_browser_action": true
  }
}
```

Planned script changes:

- [ ] Change matrix pass/fail to require:
  `expected/rubric pass AND trace acceptance pass AND artifact checks pass AND
  UI checks pass AND budget checks pass`.
- [ ] Persist acceptance details in
  `deep-research-matrix-summary.json`, not only `ok/error`.
- [ ] Add `--repetitions N` and report pass rate, median tokens, p95 tokens,
  median wall-clock, and failure classes.
- [ ] Add `--max-total-tokens`, `--max-wall-clock-seconds`, and per-scenario
  manifest budgets.
- [ ] Add milestone screenshots and DOM snapshots:
  `plan-created`, `first-source`, `first-report-write`, `first-report-patch`,
  `final`, and `after-reload`.
- [ ] Save a compact `environment.json`: git commit, model/provider, profile,
  prompt/tool preset, Phoenix status, server URL, and benchmark manifest hash.
- [ ] Add a report quality checker that reads `research/report.md`,
  `research/sources.jsonl`, and optional `research/claims.jsonl`.
- [ ] Add source-grounding checks:
  report cited URLs must be present in the ledger and have fetched/blocked
  status; verified claims must not cite search-only candidates.
- [ ] Add hard source-ladder checks:
  `source_read` before `browser_read`, `pdf_read` for PDF sources,
  `browser_action` absent unless explicitly allowed by manifest.
- [ ] Add failure classification to trace audit and matrix output.
- [ ] Keep a small manual review step: inspect one screenshot, one report, and
  one Phoenix trace per profile before marking a phase complete.

PR gate:

- 2 light live canaries.
- 1 medium live Deep Research run.
- No fake-only pass is allowed for Deep Research behavior changes.
- Matrix `ok=true` is not allowed to mean only "expected answer found".

Nightly/manual gate:

- 10 benchmark questions x selected profiles.
- Use `--profiles light,medium` for cost-controlled nightly.
- Use `--profiles light,medium,hard` before release.
- At least one medium canary must run twice and pass twice before promoting a
  prompt/tool/runtime change.

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
CHAT_DEMO_LIVE_ARTIFACT_DIR=/tmp/chat-demo-live-deep-research \
PHOENIX_COLLECTOR_ENDPOINT=http://127.0.0.1:6006/v1/traces \
DEEP_RESEARCH_PROFILES=light,medium \
DEEP_RESEARCH_LIMIT=3 \
  scripts/run_deep_research_live_observed.sh
```

Use `scripts/deep_research_live_matrix.py --dry-run` to print the matrix
without spending model tokens. Actual Deep Research live runs should use
`scripts/run_deep_research_live_observed.sh`, because it enables SQLite,
Phoenix, run-scoped artifacts, logs, and the model/tool preflight gate.

The wrapper runs `model-preflight-search-fetch` before the matrix unless the
fingerprint of relevant provider/runtime/tool/SSE/probe code matches the last
passing preflight. Use `DEEP_RESEARCH_FORCE_PREFLIGHT=1` to force it.

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
- requested research mode;
- profile;
- profile source;
- hard opt-in flags;
- run id;
- git commit;
- manifest hash;
- model;
- provider;
- final verdict;
- terminal event;
- failure class;
- tool chain;
- first tool;
- wall-clock seconds;
- prompt tokens;
- completion tokens;
- total tokens;
- token budget;
- token budget status;
- output tokens after first report update;
- search count;
- fetch count;
- fetch attempts;
- unique domains;
- verified source count;
- blocked source count;
- source_read count;
- pdf_read count;
- browser_read count;
- browser_action count;
- rendered screenshot artifact count;
- subagent child count;
- subagent duplicate count;
- report artifact present;
- source ledger present;
- claim audit present;
- report full writes;
- report patches/edits;
- stale edits;
- repeated report reads;
- repeated search queries;
- phase violations;
- long final after report;
- visible plan completion;
- expected answer found;
- report rubric score;
- source grounding score;
- process score;
- UX score;
- efficiency score;
- reliability/pass-rate bucket;
- screenshot path;
- milestone screenshot paths;
- after-reload screenshot path;
- workspace preview path;
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
- `deep_research_browser_used_before_source_read`
- `deep_research_browser_action_without_opt_in`
- `deep_research_pdf_verified_without_text`
- `deep_research_source_claim_without_ledger`
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
- Run at least one PDF-backed hard question and one browser-read fallback
  question.
- Confirm browser-action tools remain unavailable unless the explicit
  hard-browser-action flag is enabled.
- Confirm the Deep Research cockpit is understandable from screenshots without
  expanding raw tool cards.
- Confirm Markdown and PDF downloads from the UI.

## Definition Of Done

Deep Research is not considered fixed until all are true:

- Medium Deep Research can create and patch `research/report.md`.
- Medium and hard can use subagents.
- The user can explicitly select `Web`, `Deep: Medium`, or `Deep: Hard` before
  sending, and the selected mode/profile is visible in run metadata and UI.
- Fresh ordinary chats do not silently default to medium Deep Research; medium
  is the default only after Deep is selected or explicitly accepted.
- Hard profile requires visible opt-in and cannot be silently chosen by
  auto-routing.
- The parent, not children, owns final synthesis.
- Hard profile has a source-reading ladder: `source_read` first, `pdf_read`
  for PDFs, `browser_read` only as fallback, and no `browser_action` by default.
- Backend exposes a typed run-level `DeepResearchViewState` projection that
  survives reload/replay.
- The UI shows phase, todo progress, source counts, subagent state, warnings,
  and artifacts while the run is active.
- A user can open the live report artifact from the research cockpit before the
  final assistant handoff.
- The user can download Markdown and PDF when a report exists.
- Phoenix and trace-summary show token/tool/source metrics.
- Live Playwright screenshots show no 0/N plan with long answer prose above it.
- The fork-join canary passes twice on live model/provider.
- At least 8 of 10 public benchmark seed questions answer correctly in the
  selected profile, or failures are documented as source/tool/provider issues.

## Immediate Next Work Order

Revalidated on 2026-06-02 against the current code, OpenClaude, Hermes Agent,
OpenAI Deep Research docs, Claude Code subagent docs, Anthropic multi-agent
research guidance, and Deep Research Bench. The plan is still directionally
correct, but the next bottleneck is no longer "enable subagents"; it is
parent-owned synthesis after bounded child joins.

Live triage updates from 2026-06-02:

- `run_fe4335ccfc80` stopped at 35,460 tokens after child join because the
  parent continued discovery with `glob_search` instead of writing the report.
  This validated the parent-synthesis stop condition.
- `run_3832a3af0f87` stopped at 25,073 tokens after the first child join because
  the first parent-synthesis gate denied the second medium `agent_tool` too
  early. The correct rule is budget-aware: medium may launch up to 2 children,
  then must synthesize.
- `run_0efeea087fa4` stopped at 31,900 tokens because trace-summary counted the
  same second `agent_tool` twice from started/completed events. Scorecards must
  reason over completed tool calls for post-handoff budget checks.
- `run_eaa417457f2f` stopped at 21,793 tokens with a useful source ledger
  (`research/sources.jsonl`, 44 records) but no subagents and no report. This is
  the next architecture blocker: medium/hard need a strategy gate that makes
  bounded subagents mandatory when requested, then forces a write-phase
  transition once source discovery has enough evidence or exhausts the budget.

1. Finish the Phase 0.5 parent-synthesis handoff before another medium live
   spend:
   - after child joins, the parent must summarize child notes into
     `research/sources.jsonl` or an explicit parent notes file;
   - the parent must create or patch `research/report.md` itself;
   - medium readiness must reject child final auto-capture, child artifacts, and
     unsynthesized child notes.
   - implementation progress: joined child notes now create
     `deep_research_child_synthesis.pending=true`, force the phase contract
     toward `write`, and inject a parent-only artifact-write reminder. The
     remaining work is the repair/enforcement layer that turns this pending
     state into an observed parent `file_write`/`file_patch` before another long
     assistant message. The pending state is now also visible in trace-summary
     as `subagents.child_synthesis_pending`. The repair layer now sets a
     `file_write` tool-choice override when a model tries to finish while child
     synthesis is pending. The tool stage now also denies non-synthesis tools
     after the medium/hard child budget is exhausted, while still allowing the
     second medium child if the first child joined early.
2. Add a strategy gate for Deep Research profiles before the next full medium
   spend:
   - `light` must never force subagents or report artifacts;
   - `medium` must call `agent_tool` before broad serial web discovery unless
     the task contract explicitly disables subagents;
   - if `medium` skips `agent_tool`, the live probe should stop early with
     `missed_explicit_delegation`, before a search/fetch loop burns tokens;
   - after enough verified sources or repeated blocked fetches, force
     `file_write`/`file_patch` for `research/report.md` instead of allowing more
     `web_search`.
   - implementation progress: deterministic request-prep strategy choice now
     forces `agent_tool` for medium/hard after an initial todo/plan, respects
     explicit tool-choice overrides and `light`, and forces `file_write` after
     subagent use plus a small discovery budget. This still needs one observed
     medium live validation with Phoenix/SQLite/logs/screenshots.
3. Add trace-summary and scorecard fields that distinguish parent evidence from
   child evidence:
   - `parent_search_count`, `parent_fetch_count`, `parent_verified_read_count`;
   - `child_search_count`, `child_fetch_count`, `child_verified_read_count`;
   - `child_summary_chars`, `duplicated_child_queries`, `child_count`.
4. Add a medium live abort if child joins are complete but the next parent LLM
   step does not call `file_write`, `file_patch`, `file_edit`, `read_file`,
   `artifact_preview`, or a ledger-writing tool before producing long prose.
5. Re-run the short model/tool preflight only if the fingerprint changed.
6. Re-run one observed `light` canary with SQLite/Phoenix/logs/screenshots:
   expect short answer, no subagents, no report, no Deep Research skill unless
   explicitly requested, and profile-specific source thresholds.
7. Re-run one observed `medium` fork-join canary:
   expect report visible in workspace, trace-summary, and UI; no unknown tools;
   no child artifact substitution; no 100k-token runaway; any remaining failure
   should be source quality or research completeness, not runtime contract.
8. After the medium canary passes twice, continue Phase 1/2 implementation work:
   capability surface cleanup, artifact-first controller gates, durable UI
   cockpit, and reload hydration.
9. Implement `source_read` over existing `web_fetch` and source ledger before
   adding any browser fallback.
10. Add `pdf_read` with validation/page-range extraction and hard-profile trace
   metrics.
11. Add raw Markdown/PDF endpoints and download buttons.
12. Add `browser_read` only as hard fallback with security tests and live
    screenshot artifacts.
13. Compare architecture variants A/B/C only after the medium baseline is
    contract-correct and passes the fork-join canary twice.
14. Run hard profile with verifier/auditor, PDF, browser-read fallback, and
    export checks.
