# Unified Work Plan

Дата: 2026-05-31.

Цель: связать текущие направления `agent-driver` в один порядок работ:
research/provider quality, Deep Research + Skills, harness/context pressure,
SDK productization, refactoring and docs cleanup.

Короткий вердикт: **сначала укрепляем измерение и state discipline, потом
меняем поведение, затем упаковываем SDK и только после этого чистим/двигаем
крупные модули и документацию**. Иначе мы либо зафиксируем грязные internal
metadata как публичный SDK, либо добавим Skills/Deep Research поверх уже
перегруженных stage-файлов.

## Inputs

Active / semi-active planning docs:

- [Research Quality Summary](research-quality-improvement-plan-2026-05-31.md)
- [Provider And Model Debugging](provider-model-debugging.md)
- [Deep Research And Skills Analysis](deep-research-and-skills-analysis-2026-05-31.md)
- [SDK Quality Deep Analysis](sdk-quality-deep-analysis-2026-05-31.md)
- [Agent Driver Refactoring Plan](agent-driver-refactoring-plan-2026-05-31.md)
- [Additional Notes](add-notes.md)

Closed / noisy but valuable history:

- [OpenClaude/Hermes Improvement Plan](openclaude-improvement-plan-2026-05-29.md)
- [Chat Demo Markdown And Citations Plan](chat-demo-markdown-citations-plan-2026-05-30.md)
- [Chat Demo Compaction Notification Plan](chat-demo-compaction-notification-plan-2026-05-30.md)
- [Research Provider Quality Architecture Plan](research-provider-quality-architecture-plan-2026-05-31.md)

External context note:

- HumanLayer's "Advanced Context Engineering for Coding Agents" argues for
  frequent intentional compaction, keeping complex coding-agent workflows
  around roughly 40-60% context utilization, and using subagents as context
  control rather than as role-play:
  <https://github.com/humanlayer/advanced-context-engineering-for-coding-agents/blob/main/ace-fca.md>
- External re-check on 2026-05-31 confirms the same product/runtime split:
  OpenAI Deep Research exposes long-running/background runs, output items for
  web/file/MCP/code activity and clickable citations; OpenAI Skills and
  Anthropic Agent Skills both treat skills as portable `SKILL.md` workflows
  with metadata-first discovery, lazy body/supporting-file loading and trust
  review. Sources:
  <https://developers.openai.com/api/docs/guides/deep-research>,
  <https://help.openai.com/en/articles/20001066-skills-in-chatgpt>,
  <https://academy.openai.com/public/resources/skills>,
  <https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills>,
  <https://www.anthropic.com/engineering/built-multi-agent-research-system>,
  <https://www.anthropic.com/engineering/building-effective-agents>.

## Current State

### What Is Already Mostly Closed

The following should not be reopened as open-ended architecture debates unless
new Phoenix traces show a fresh regression:

- dynamic prompt assembly by effective tool surface;
- chat planning split: live todos vs modal approval planning;
- steering controls and queue semantics;
- subagent basics: spawn, child rows, join, synthesis UI;
- Python tool autonomy and UI;
- Markdown/math/code rendering and citation shelf;
- compaction notification lifecycle;
- research evidence gate: `web_search` candidates vs `web_fetch` verified
  evidence;
- provider failure UX for 4xx/stream errors;
- unknown-tool repair and bounded research repair.

These plans are useful as decision logs, but they are too long for the active
docs front door.

### What Is Still Active

1. Provider/model research quality has a completed baseline for the fork-join
   scenario and must keep the cheap-to-expensive live matrix discipline from
   `provider-model-debugging.md` for future provider/research changes.
2. `skill_tool` is only discovery; it does not yet implement metadata parsing,
   `skill_view`, invocation records, subagent preload or compaction survival.
3. Runtime state is still too dependent on ad hoc `context.metadata[...]`.
   Planning tools show the same smell at the public tool boundary:
   `exit_plan_mode_v2` is the only registered approval-exit tool, while older
   names such as `exit_plan_mode` still appear in runtime checks/history. The
   issue is not "two tools" so much as versioned tool names leaking into
   prompts, traces, tests and future SDK contracts.
4. Context pressure behavior is late; `add-notes.md` correctly points out that
   92% is emergency territory, not the first moment to summarize/delegate.
5. SDK shape is too close to internal runtime: no first-class `Session`,
   `RunHandle`, stream helper, typed SDK errors or stable trace contract.
6. Large modules are now friction points:
   `tool_stage.py`, `llm_step.py`, `openai_compatible.py`,
   `governed.py`, `run_trace_summary.py`.
7. Docs have accumulated closed plans that obscure the active sequence.

## Principle Fit And Product Boundary

The OpenClaude/Hermes principles are achievable in this plan if we keep one
non-negotiable boundary:

**`agent_driver` owns reusable runtime/product logic; `examples/chat-demo`
owns only UX composition, local FastAPI/React wiring, settings, scenario
fixtures and visual regression checks.**

That means:

- Deep Research mode can appear in chat-demo as a button, segmented mode,
  progress surface, source shelf, citation inspector and run-history UX. The
  button must map to shared runtime contracts such as `research_depth`,
  `ResearchSessionContract`, source ledger, context-pressure diagnostics and
  optional subagent strategy. It must not implement private research logic in
  the demo backend/frontend.
- Skills can appear in chat-demo as skill library management, install/upload,
  trust warnings, skill picker, invocation trace and supporting-file preview.
  Parsing `SKILL.md`, trust classification, skill registry, `skill_view`,
  invocation records, allowed-tools policy, compaction survival and
  skill-aware subagent preload belong in `agent_driver`.
- Chat-demo may keep thin adapters that translate UI state into
  `AgentRunInput`, `ToolPolicyInput` or future SDK/session calls. If a behavior
  would also be needed by CLI, SDK, another backend, or eval harness, it belongs
  in `agent_driver`.
- New UI affordances should follow shared runtime events/metadata first. If the
  UI needs a new event, state or trace field, add it to runtime contracts before
  rendering it in the demo.

### Problem Escalation Principle

Do not ignore principle violations or architectural drift.

- If a problem is clear and the fix is local, low-risk and fits the current
  context, fix it immediately.
- If the fix is clear but not part of the current slice, add it to the
  appropriate later phase or status doc with enough detail that it cannot be
  forgotten.
- If the problem violates these principles but the right fix is ambiguous,
  risky, product-sensitive or needs ownership/tradeoff decisions, stop and
  surface it for human decision instead of silently choosing a direction.
- Do not let "not now" become "never": every observed principle violation must
  be fixed, explicitly scheduled or explicitly escalated.

Principle audit:

| Principle | Current plan fit | Required guardrail |
| --- | --- | --- |
| Python Zen / simple, readable, testable | Mostly fit: phases favor contracts, small helpers, trace gates | Keep Deep Research as contract + ledger + optional subagents, not a generic DAG |
| Model + prompt + small runtime guard first | Fit: research repair, tool-surface prompts and context nudges follow this | Add orchestration only after trace/eval failures show the simple loop is insufficient |
| Complex orchestration only from traces/tests | Fit if Phase 5 subagents remain optional and gated | Add eval labels for when subagents were necessary vs wasteful |
| Simplify when possible | Fit: metadata owner map and module splits reduce hidden coupling | Reject chat-demo-only feature forks that duplicate runtime behavior |
| Chat demo stays clean | Needs explicit enforcement | Treat chat-demo as product integration gate, not the owner of Deep Research/Skills logic |

## Dependency Graph

| Workstream | Depends On | Blocks / Enables |
| --- | --- | --- |
| Provider/research verification gate | Existing trace summary, live probe | Safe research/skills changes |
| Metadata inventory + typed runtime state | None | Refactor, SDK contracts, context pressure |
| Contract snapshots | Metadata inventory | SDK stabilization, refactor safety |
| Context pressure / early compaction | Typed state, trace diagnostics | Better long research/code tasks, SDK diagnostics |
| Skill metadata + `skill_view` | Tool catalog projection, trust policy | Research skills, skill-aware subagents |
| Research + Skills integration | Skill core, source evidence ledger | Deep Research quality without DAG |
| Chat-demo research/skills UX | Research/skill runtime contracts, SDK/session helpers | Product validation without logic duplication |
| SDK productization | Contract snapshots, stable state names | Public API/docs, package consumer tests |
| Structural refactor | Contract snapshots, focused behavior tests | Long-term maintainability |
| Docs cleanup | Unified plan, status map | Lower planning noise |

The important ordering constraint: **do not freeze public SDK APIs before
metadata/state contracts are explicit**, and **do not add a Deep Research DAG
before Skills + evidence ledger + trace gates are tested**.

## Benchmark Strategy

Research and harness quality should not be validated by a hand-picked set of
ten happy-path prompts. At the same time, adopting a huge external benchmark as
the daily gate would make iteration slow, expensive and noisy. Use a layered
benchmark strategy.

## Phase-End Quality Gate

After every phase, run a real code-quality pass over the packages touched by
that phase:

- `ruff check` over touched Python files/packages.
- focused `pytest` plus the relevant broader non-live regression gate.
- `pylint` over touched runtime/domain packages with the existing project
  configuration and a non-lowered `--fail-under` threshold. Fix real
  `error`/`warning` findings and any new actionable convention/refactor
  findings. Do not hide phase regressions with broad disable pragmas or by
  weakening the threshold.
- If `pylint` reports pre-existing structural debt outside the phase boundary,
  record it as a later refactor item instead of silently treating the phase as
  clean.

### Relevant External Benchmarks

Deep Research / browsing:

- **BrowseComp**: OpenAI benchmark for hard-to-find factual answers that
  require persistent browsing. Good for search strategy and source discovery,
  but vulnerable to contamination and often too expensive for daily runs.
  Source: <https://openai.com/index/browsecomp/>
- **GAIA**: real-world assistant tasks requiring reasoning, tool use, web
  browsing and often file/multimodal handling. Good for general agentic
  behavior, but broader than our research contract.
  Source: <https://ai.meta.com/research/publications/gaia-a-benchmark-for-general-ai-assistants/>
- **FRAMES**: 824 multi-hop retrieval/reasoning questions over Wikipedia
  evidence. Good for retrieval/source synthesis and cheaper to adapt to
  controlled corpora than open web.
  Source: <https://huggingface.co/datasets/google/frames-benchmark>
- **BrowseComp-Plus / fixed-corpus derivatives**: useful because a fixed corpus
  makes runs more reproducible than live web, though we should treat newer
  derivatives as research inputs until their tooling is stable.
  Source: <https://arxiv.org/abs/2508.06600>

Harness / full agent evaluation:

- **Harness-Bench**: evaluates harness/configuration effects across sandboxed
  offline tasks with file operations, shell, browser interactions and artifact
  grading. Relevant to our concern that model quality and harness quality get
  mixed together.
  Source: <https://www.harness-bench.ai/>
- **ClawBench / OpenClaw-style trace benchmarks**: useful design pattern:
  trace-based scoring, failure modes such as hallucinated completion,
  verification skipped, tool misuse, state regression. Treat as methodology
  inspiration even if we do not adopt the suite wholesale.
  Source: <https://github.com/openclaw/clawbench>
- **ProofAgent Harness / similar local harnesses**: useful for adversarial
  multi-turn behavior, policy edges and behavioral regression testing.
  Source: <https://www.proofagent.ai/harness>
- **SWE-bench, Terminal-Bench, WebArena, OSWorld, tau-bench**: useful for
  broader agent capability comparisons, but most are not direct gates for our
  current research/provider/skills work.

### Recommended Use

Use external benchmarks as **calibration and seed pools**, not as the only
definition of done.

Daily / per-PR gate:

- 8-15 internal scenarios, deterministic or fake-provider where possible.
- Trace-level assertions, not just final text:
  tool order, fetch count, source diversity, final citation coverage,
  unfinished todos, unknown tools, provider terminal state, context pressure
  state, compaction events, child handoff.
- These should run quickly and fail with actionable labels.

Weekly / phase gate:

- 20-40 scenarios total:
  internal scenarios plus a small stratified sample from external benchmarks.
- Suggested sample:
  5 BrowseComp-style hard lookup tasks;
  5 GAIA-style multi-tool tasks;
  5 FRAMES-style multi-hop retrieval tasks;
  5 harness stress tasks with bad JSON, blocked fetch, provider 4xx, stalled
  stream, context pressure, compaction/resume, subagent join.
- Run each stochastic live scenario 3 times or on 2 model classes when judging
  model-dependent behavior.

Release / confidence gate:

- 50-100 scenario matrix, not necessarily all live web.
- Include fixed-corpus tasks to avoid live-web drift.
- Include current production-like prompts from our own users/docs.
- Track trend over time: pass rate, failure labels, token cost, latency,
  source coverage, provider failures, context-pressure outcomes.

### Why Not Only External Benchmarks

- Many public benchmarks measure "model + harness + provider + tool stack" as
  one number. That is useful for marketing but weak for engineering diagnosis.
- Live web tasks drift; exact answers and pages can disappear.
- Public questions can be contaminated, and Anthropic has documented eval
  awareness/contamination concerns around BrowseComp-style evaluation:
  <https://www.anthropic.com/engineering/eval-awareness-browsecomp>.
- Benchmarks often score only final answers, while our risk is hidden runtime
  behavior: fake sources, unfinished todos, bad tool recovery, progress-only
  finals, provider protocol failures.

### Why Not Only Our Own Tests

- Internal tests overfit quickly. A plan can look "closed" after ten examples
  and still fail on unfamiliar search strategies.
- External samples add distribution pressure: obscure factual lookup,
  multi-hop retrieval, mixed tools, file/code reasoning and adversarial harness
  faults.
- External benchmark task taxonomies help us notice missing scenario classes.

### Target Dataset Shape

Create an `eval_scenarios/research_harness/` or equivalent fixture set with
explicit tags:

- `research_depth`: `light_search`, `source_verified_report`,
  `deep_parallel_research`;
- `source_mode`: `live_web`, `fixed_corpus`, `file_search`, `mcp_resource`;
- `failure_mode`: `none`, `provider_4xx`, `fetch_blocked`, `bad_json`,
  `unknown_tool`, `context_pressure`, `compaction_resume`;
- `expected_tools`: `web_search`, `web_fetch`, `python`, `agent_tool`,
  `skill_view`;
- `scoring`: deterministic, trace-contract, LLM-judge, human-review.

The default engineering score should be trace-contract first, final-answer
judge second.

## Optimal Sequence

### Phase 0 - Plan Triage And Live Gate Baseline

Purpose: establish a single truth for what is active and make every later
change measurable.

- [x] Treat this document as the active sequence for cross-cutting work.
- [x] Finish/update the model matrix in
  [Provider And Model Debugging](provider-model-debugging.md): cheap models
  first, GPT-5.5 only as final acceptance or model-specific reproduction.
- [x] Add or refresh one trace artifact per important research/provider failure
  class: provider 4xx, search-only candidate, missing source diversity,
  unfinished todos, progress-only final.
- [x] Convert `docs/add-notes.md` into a real context-pressure plan or fold its
  content into Phase 2 below.
- [x] Do not delete closed docs yet; first mark their status.

Acceptance:

- We can say which live scenario proves a change fixed research/provider
  behavior.
- Active docs vs closed history are identified.

Status on 2026-05-31:

- GPT-5.5 final acceptance passed for `research-report-requires-fetch` as
  `run_657ce790e764`.
- Adjacent model acceptance passed on DeepSeek, GLM, Kimi, Qwen 3.7 and Claude
  Sonnet 4.6; see [Provider And Model Debugging](provider-model-debugging.md).
- Broad deterministic regression passed after the live acceptance. Remaining
  Phase 0 work is closed: docs/status hygiene was completed without deleting
  historical plans.

### Phase 1 - Runtime State And Contract Foundation

Purpose: stop new work from deepening the metadata bag problem.

- [x] Execute refactoring Phase 1: inventory every `context.metadata[...]` key
  with owner, producer, consumer, persistence need and UI relevance.
- [x] Add `docs/runtime-metadata.md` as the metadata owner map.
- [x] Execute refactoring Phase 2 in a compatibility-preserving way:
  `LoopControlState`, `ToolLoopState`, `PlanningRuntimeState`,
  `ResearchRuntimeState`, `StreamingRuntimeState`,
  `CompactionRuntimeState`.
  Initial metadata views exist in `agent_driver/runtime/metadata_state.py`;
  migration of direct reads/writes is intentionally incremental. 2026-05-31
  update: explicit `get_*_state(context)` helpers were added; `RunContext`
  loop/tool counters, terminal-output lookup, workspace-cwd lookup, planning
  event emission and forced-final/tool-choice paths in `tool_stage.py` now use
  the typed views while preserving the legacy metadata keys. A second slice
  moved terminal/paused output compaction projection, interrupt payload,
  approved-plan lookup, raw assistant content and stream recovery bookkeeping
  behind the same views. Final Phase 1 closure moved research contract,
  tool-result consumers, todo reminder counters, planning updates, LLM
  trim/microcompaction payloads, tool-choice reads and source-verified repair
  paths behind those views. Remaining direct writes in producer stages such as
  `compaction_stage.py`, `resume.py` and subagent bookkeeping are owned by
  later structural workstreams and do not create new public SDK metadata
  contracts.
- [x] Add contract snapshots for public shapes:
  `AgentRunInput`, `AgentRunOutput`, `RuntimeEvent`, `ToolManifest`,
  `ToolTrace`, interrupt/resume payloads.
- [x] Define a canonical planning tool contract before SDK freeze:
  `exit_plan_mode_v2` stays the canonical public approval-exit tool name;
  `exit_plan_mode` is documented and handled only as a legacy trace alias.
  Prompts, `PLANNING_TOOL_NAMES`, trace summaries, tests and docs now use that
  vocabulary.
- [x] Add a rule: new runtime state must go through owned helpers, not fresh
  ad hoc metadata keys.

Acceptance:

- Existing `AgentRunOutput.metadata` remains behavior-compatible.
- Refactor and SDK work can proceed with stable internal state boundaries.
- Planning approval no longer exposes accidental tool-name versioning as an
  architectural decision.

Status on 2026-05-31:

- Phase 1 is closed for the six planned state groups: loop control, tool loop,
  planning, research, streaming and compaction.
- High-churn consumers in `steps.py`, `llm_step.py`, `tool_stage.py`,
  `output.py`, `todo_reminders.py`, `step_planning.py`,
  `research_session_contract.py` and `subagent_stage.py` now use typed metadata
  views for the migrated state paths while preserving legacy serialized keys.
- Compatibility tests cover helper shape preservation and the non-live runtime
  regression gate passed.

### Phase 2 - Harness Context Pressure

Purpose: address the "dumb zone" before it silently degrades research/code
quality.

- [x] Add `context_usage_ratio` to token pressure snapshots.
- [x] Add states:
  `ok`, `early_warning`, `delegate_or_summarize`,
  `compact_recommended`, `blocking`.
- [x] Emit runtime/stream diagnostics when state changes.
- [x] Add model-facing nudges around 35-45% context usage:
  summarize findings, delegate read-heavy work, preserve source refs, move to
  synthesis.
- [x] Keep 92% as emergency compaction/blocking, not normal operating policy.
- [x] Add eval/live scenario for a long research/code task comparing behavior
  before and after early pressure nudges.

Acceptance:

- Long tasks get early structured guidance before context is already bad.
- Trace summary can explain whether the run ignored a context recommendation.

Status on 2026-05-31:

- Token pressure snapshots now use the full Phase 2 state ladder. The default
  soft threshold starts near 35% context usage, `delegate_or_summarize` starts
  near 45%, `compact_recommended` remains the compaction trigger, and
  `blocking` is the emergency guard at roughly 92% usage.
- Runtime warning/stream diagnostics are emitted only when the pressure state
  changes and include stable signal ids, severity, `context_usage_ratio` and a
  recommendation slug.
- The current LLM request receives a context-pressure system nudge for
  early-warning/delegation/compaction/blocking states so the model can preserve
  source refs, summarize read-heavy findings, delegate separable work or move
  toward synthesis before emergency compaction.
- `summarize_run_trace` now exposes `context_pressure` diagnostics including
  states, recommendations, delegation/compaction reaction and whether the latest
  recommendation appears ignored. Focused deterministic tests cover the long
  research/code pressure path; live comparison remains optional calibration, not
  a per-change gate.

### Phase 3 - Minimal SDK P0

Purpose: package existing capabilities without exposing unstable internals.

This phase should be small and compatibility-preserving; do not wait for every
refactor phase before making the SDK pleasant.

- [x] Add `agent.query(...)` / top-level `query(...)` for one-shot usage.
- [x] Add `Session` facade:
  `send`, `stream`, `resume`, `history`, `runs`, `fork` where already
  supported by stores.
- [x] Add `RunHandle`:
  `run_id`, `events()`, `final()`, `abort()`, `checkpoint()`.
- [x] Add stream helper object over the existing event log:
  `events`, `text_deltas`, `final_output`, `cancel`, `cursor`.
- [x] Add package import isolation tests for `agent_driver.sdk`.
- [x] Keep `graph_preset` and low-level `SingleAgentRunner` as advanced escape
  hatches, not quick-start API.

Acceptance:

- A backend developer can build a chat endpoint without importing
  `runtime.single_agent`.
- SDK does not leak CLI/TUI/chat-demo dependencies.
- Chat-demo can migrate toward SDK/session calls without becoming the owner of
  reusable runtime behavior.

Status on 2026-05-31:

- SDK P0 exposes `Agent.query`, top-level `query`, thread-scoped `Session`,
  `RunHandle` and object-oriented `RunStream` helpers over the existing runner,
  stores, checkpoint store and event log.
- `Session` covers `send`, `stream`, `resume`, `history`, `runs`, `start` and
  `fork`; the low-level `SingleAgentRunner` remains available through
  `Agent.runner` for advanced embedders.
- SDK import isolation is covered so importing `agent_driver.sdk` does not pull
  CLI or chat-demo modules.

### Phase 4 - Skill Core

Purpose: turn Skills from filesystem discovery into a real runtime context
mechanism.

- [x] Add `agent_driver.skills` package with `SkillManifest` and frontmatter
  parser.
- [x] Extend/replace `skill_tool` with metadata listing:
  `name`, `description`, `when_to_use`, `version`, `tags`, `allowed_tools`,
  `context`, `agent`, `paths`, `trusted`, `source`, `skill_dir`.
- [x] Add `skill_view`: load full `SKILL.md` or one supporting file.
- [x] Return supporting file index, skill directory, trust and safety warnings.
- [x] Record `SkillInvocation` in runtime events/metadata.
- [x] Add prompt fragment: call `skill_view` when a skill is relevant; do not
  merely mention a skill without loading it.
- [x] Add compaction persistence for invoked skill refs: name, path, digest,
  trusted flag, agent id; do not persist full bodies by default.
- [x] Keep all shared skill behavior in `agent_driver`: frontmatter parsing,
  trust classification, registry/listing, `skill_view`, invocation records,
  allowed-tools policy and compaction survival.

Acceptance:

- Skills are visible, loadable, traceable and safe enough for curated research
  work.
- Full skill content is loaded only on demand.
- Chat-demo can render skills and skill warnings using shared contracts without
  parsing or executing skills itself.

Status on 2026-05-31:

- Added `agent_driver.skills` with `SkillManifest`, `SkillInvocation`,
  conservative `SKILL.md` frontmatter parsing, trust classification,
  supporting-file indexing, metadata listing and on-demand view helpers.
- `skill_tool` now returns metadata-first skill rows while preserving old path
  and provenance fields; `skill_view` loads either the skill body or a single
  supporting file and returns safety warnings plus a compact invocation record.
- Runtime records `skill_view` usage as `skill_invoked` events and persists
  `skill_invocations` / `invoked_skill_refs` in output metadata and compaction
  projection without storing full skill bodies.
- Prompt assembly now includes a skills policy fragment when `skill_tool` or
  `skill_view` is in the effective tool surface.

### Phase 5 - Research + Skills Integration

Purpose: improve Deep Research quality without building a separate research
DAG.

- [x] Add curated research skills:
  `deep-research-report`, `source-triangulation`,
  `provider-doc-research`, `literature-review`, `citation-auditor`.
- [x] For `source_verified_report`, suggest relevant skills when `skill_view`
  is available; do not auto-load hidden instructions.
- [x] Promote source evidence into a first-class ledger:
  search candidates, verified web/file/MCP reads, failed/blocked reads,
  assistant links.
- [x] Keep `ResearchSessionContract` as final-readiness authority.
- [x] Add optional skill-aware subagent preload:
  child receives trusted skill bodies, parent receives compact findings +
  source refs.
- [x] Add a provider-neutral Deep Research mode contract:
  `research_depth=deep_parallel_research`, progress events, source ledger,
  context-pressure recommendations, optional child strategy and final citation
  coverage. Provider-native Deep Research adapters may feed this ledger, but
  must not become the only implementation.
- [x] Add live/fake evals:
  deep report with skill loaded, literature review, provider-doc official-only,
  malicious/untrusted skill, compaction after skill invocation.

Acceptance:

- Skills improve research behavior but cannot launder search candidates into
  verified sources.
- Parent still owns final synthesis and citation coverage.
- Deep Research remains a shared runtime contract; chat-demo only selects,
  displays and verifies it.

Status on 2026-05-31:

- Bundled curated research skills live under `agent_driver.skills.curated` and
  are discoverable through the normal skill registry.
- Runtime reminders suggest curated skills for `source_verified_report` only
  when `skill_view` is available; skill bodies are not auto-loaded.
- `ResearchSessionContract` now exposes a `source_ledger` with candidate,
  verified, failed/blocked and assistant-link rows; readiness still depends on
  verified reads rather than search candidates.
- `deep_parallel_research` is accepted as a provider-neutral depth with a
  mode payload for progress event names, source ledger, context-pressure
  recommendations, optional child strategy and final citation coverage.
- Subagent groups may opt into `skill_preload=trusted_viewed`; only trusted
  viewed skill bodies are passed to child task metadata/input, while the parent
  remains responsible for final synthesis.
- Focused fake regression coverage was added for curated skill discovery,
  source-ledger non-laundering, deep-research readiness authority, skill
  suggestion prompts and trusted-only subagent preload. Live provider probes
  remain part of the weekly/phase live matrix rather than the default local
  gate.
- Live OpenRouter sanity was run before Phase 5A with
  `AGENT_DRIVER_RUN_LIVE_TESTS=1`, model `qwen/qwen3.6-plus`:
  `tests/runtime/live_smoke/test_deep_research_skills.py -m live` and
  `tests/runtime/test_live_context_quality_openrouter.py -m live` both passed.

### Phase 5A - Chat-Demo Deep Research And Skills UX

Purpose: expose the new runtime capabilities in the product surface without
moving product logic into the demo.

- [x] Add a Deep Research affordance only after the runtime exposes the shared
  mode contract: button/segmented mode, progress surface, source/citation
  inspector, context-pressure status, child-run panel and final-readiness
  diagnostics.
- [x] Add Skills UX only after `agent_driver.skills` and `skill_view` exist:
  skill library list, install/upload flow, trust/review warnings, skill picker,
  invocation timeline and supporting-file preview.
- [x] Backend endpoints must be thin adapters over SDK/runtime contracts:
  no local `SKILL.md` parser, no private evidence ledger, no demo-only research
  readiness logic.
- [x] Frontend state should render runtime events and stable contracts, not
  infer hidden research/skill state from assistant prose.
- [x] Add deterministic fake scenarios plus at least one live probe for:
  deep research progress, skill load, untrusted skill warning, citation shelf,
  compaction after skill invocation and provider failure after search.

Acceptance:

- Chat-demo proves the UX, accessibility and traceability of Deep
  Research/Skills while remaining replaceable by another frontend.
- A second app can reuse the same SDK/runtime contracts without copying
  chat-demo code.

Status on 2026-05-31:

- Chat-demo now exposes a Deep mode toggle that sends
  `research_depth=deep_parallel_research` and renders runtime source-ledger /
  progress diagnostics from SSE events.
- Chat-demo now exposes a Skills panel backed by thin `/api/skills` adapters
  over `agent_driver.skills` / `skill_view`; upload installs a demo-local
  `SKILL.md` and reuses the shared registry parser.
- The default web/safe tool presets include the discovery tool pack, so
  `skill_tool` and `skill_view` are visible through the public tools endpoint
  and the normal ReAct prompt surface.
- Added a deterministic `deep_research_skills` fake scenario that creates a
  workspace-local skill, invokes `skill_view`, fetches `https://example.com`,
  and emits `source_ledger_updated` through the runtime event stream.
- Focused backend/frontend tests pass for the new adapters, SSE event flow,
  source-ledger parsing and replay rendering. Browser smoke is blocked locally
  because Playwright is not installed in the project environment; ESLint is
  blocked before linting because the project still has legacy `.eslintrc.cjs`
  while ESLint 9 expects `eslint.config.*`.
- The broader chat-demo matrix now has deterministic SSE scenarios for skill
  load plus source ledger, untrusted skill warning, compaction lifecycle after
  skill invocation and provider rejection after mock web search. Live provider
  sanity remains an opt-in acceptance probe using the existing live matrix
  credentials rather than a default local gate.

### Phase 6 - Structural Refactor

Purpose: reduce maintenance cost after state and behavior gates exist.

Do this in small PRs; avoid combining file moves with behavior changes.

- [ ] Repackage `runtime/single_agent` into lifecycle, llm call, tool loop,
  finalization, planning and context-management subpackages with compatibility
  shims.
- [ ] Split `tool_stage.py` around transitions, research controls, planning
  events and subagent outputs.
- [ ] Split `llm_step.py` around request preparation, compaction integration,
  provider call, event projection and provider error mapping.
- [ ] Turn `GovernedToolExecutor` into an explicit pipeline:
  normalize -> hooks -> policy -> gate -> partition -> execute -> collect.
- [ ] Decompose OpenAI-compatible provider adapter:
  wire payload, response parser, stream parser, usage parser, tool choice.
- [ ] Split `run_trace_summary.py` into analyzers:
  research, provider, tools, compaction, planning, streaming.

Acceptance:

- Large files stop being the default landing zone for unrelated features.
- Tests prove public behavior and event contracts stayed stable.

Status on 2026-05-31:

- Started Phase 6 with compatibility-preserving `tool_stage.py` splits:
  planning lifecycle event projection moved to
  `runtime/single_agent/tool_stage_planning.py`, and subagent post-processing
  for `agent_tool`, `send_message_tool` and `task_stop_tool` moved to
  `runtime/single_agent/tool_stage_subagents.py`.
- Source-verified research repair, web-fetch verification hints and research
  final-readiness helper checks moved to
  `runtime/single_agent/tool_stage_research.py`, leaving `tool_stage.py` as the
  transition/protocol compatibility layer.
- Started the `llm_step.py` split by moving context-pressure nudges and warning
  event projection to `runtime/single_agent/llm_step_context_pressure.py` while
  preserving the existing `llm_step.py` private helper imports used by focused
  tests.
- Provider HTTP error parsing, forced-tool request narrowing, reasoning-echo
  stripping, max-token retry shaping and no-tools retry request shaping moved to
  `runtime/single_agent/llm_step_provider_requests.py`; the LLM step keeps the
  retry loop and runtime event emission.
- ReAct system-prompt composition, chat/runtime attachment reminders, prompt
  surface metadata capture and effective code-agent import resolution moved to
  `runtime/single_agent/llm_step_prompt.py`.
- `tool_stage.py` remains the compatibility entrypoint for
  `execute_tool_stage_step`; focused planning, subagent and chat-demo Deep
  Research/Skills SSE tests pass after the extraction.

### Phase 7 - SDK P1 And Documentation

Purpose: finish productization once internals have stable owners.

- [ ] Add SDK typed provider errors, request IDs, timeout/retry config.
- [ ] Polish custom tool API:
  docstring/signature defaults, `tool(...)` helper, catalog projections.
- [ ] Pay down pre-existing SDK lint debt in `sdk.subagent` and
  `sdk.resume_payload` surfaced during Phase 3 P0 (`too-many-instance-attributes`,
  protected default access, broad exception catch, line-length cleanup).
- [ ] Add stable `TraceSummary` SDK contract and support-bundle recipe.
- [ ] Add SDK context diagnostics:
  `output.context.pressure`, `output.context.recommendation`.
- [ ] Add docs:
  `docs/sdk.md`, `docs/sdk-sessions.md`, `docs/sdk-tools.md`,
  `docs/sdk-streaming.md`, `docs/sdk-errors.md`.
- [ ] Rewrite README quick start around SDK entrypoints.

Acceptance:

- SDK is usable as a product surface, not just a wrapper over internal runner
  wiring.

### Phase 8 - Docs Cleanup

Purpose: keep docs useful without losing decision history.

Do after Phase 0 status marking and after the new active sequence is accepted.

- [ ] Add status labels to long plan docs:
  `active`, `closed`, `reference`, `archive-candidate`.
- [ ] Move closed long plans to `docs/archive/2026-05/` or compress them into
  short decision summaries.
- [ ] Keep only active/current docs in `docs/README.md` and `docs/roadmap.md`.
- [ ] Replace `docs/add-notes.md` with a real plan or delete it after its only
  note is absorbed.
- [ ] Keep `provider-model-debugging.md` active until the live model matrix is
  no longer changing.
- [ ] Keep `research-quality-improvement-plan-2026-05-31.md` as a status page,
  but link forward to this unified plan and the skills/research plan.
- [ ] Preserve test artifacts and historical run IDs only where they explain a
  current regression or acceptance gate.

Acceptance:

- A new contributor can open `docs/README.md` and see current concepts plus one
  active roadmap, not several completed phase logs.

## What Not To Do Yet

- Do not build a generic DAG/state-machine runner for research. Use skills,
  source ledger, bounded repair and subagent handoff first.
- Do not start broad module moves before metadata inventory and contract
  snapshots.
- Do not freeze SDK metadata fields until typed runtime state exists.
- Do not add automatic skill loading from semantic guesses; make skill use
  visible through `skill_view` and traces.
- Do not implement Deep Research or Skills as chat-demo-only behavior. The demo
  may expose buttons, settings, progress and management UI, but the contracts,
  ledgers, parsing, trust policy and runtime decisions belong in
  `agent_driver`.
- Do not delete historical docs until links are updated and closed status is
  recorded.

## Recommended First Sprint

If we want the next slice to be maximally useful and low-regret:

1. Done: create `docs/runtime-metadata.md` inventory.
2. Add minimal typed state helpers for research/planning/compaction/tool loop.
3. Add contract snapshots for public outputs and runtime events.
4. Normalize the planning approval tool contract:
   canonical name, legacy alias policy, prompt text and trace labels.
5. Done: promote `add-notes.md` into context-pressure acceptance criteria.
6. Refresh the provider live matrix with one cheap model and one reasoning
   model.
7. Only then start `skill_view` and SDK `query/session` slices.

This sequence keeps the runway clean: every later feature either becomes a
small contract change, a skill catalog change, or an SDK wrapper change, not
another hidden metadata convention.
