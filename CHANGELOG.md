# Changelog

All notable changes to `agent-driver` are recorded here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); the project is pre-1.0 (`0.x`),
so the public surface (see [docs/embedding.md](docs/embedding.md)) may still
change between minor versions.

## [Unreleased]

### Changed

- **Real YAML parsing for `SKILL.md` frontmatter (skills epic S4).** The hand-rolled
  frontmatter parser only reached one level of nesting and silently mis-parsed block
  scalars, lists of maps, and deeper structures. `parse_frontmatter` now uses PyYAML's
  `BaseLoader`, which handles the full YAML grammar while loading every scalar as a
  **string** — deliberately string-first to preserve the legacy semantics and sidestep
  YAML's implicit-typing footguns (`version: 1.0` → `"1.0"` not `1.0`; the Norway problem
  where `tags: [no, yes]` would otherwise become `[False, True]`). BaseLoader cannot
  construct arbitrary Python objects, so it stays safe on untrusted skill files. PyYAML is
  now a declared dependency (it had been only transitive); the conservative hand-rolled
  parser is retained as a fallback for when PyYAML is absent or the frontmatter is invalid
  YAML.
- **Memoized skill-manifest parsing (skills epic S3).** `list_skill_manifests` / `view_skill`
  each `rglob` the skill tree and re-`read_text`+parse every `SKILL.md` on every call — and
  `load_skill_manifest` `rglob`s a second time per skill for the supporting-file index — so
  discovery was O(tree) parse work per call (now repeated once per run by the S1 catalog and
  again by each `skill_tool`/`skill_view`). `load_skill_manifest` now memoizes the parsed
  manifest keyed on `(path, trusted_roots, base_dir, max_files)` and invalidates on the
  `SKILL.md`'s `(mtime_ns, size)`, so an edited skill is re-parsed (hot-reload) while
  unchanged skills are served from cache. Cache is process-wide, bounded (cleared past 1024
  entries), and exposes `clear_skill_manifest_cache()` for tests/explicit invalidation.
  Caveat: a change to a *supporting* file that leaves `SKILL.md` untouched isn't detected
  until `SKILL.md` changes.

### Fixed

- **Skill bodies stay recoverable in history (skills epic S2).** A `skill_view` result
  carries the full `SKILL.md` body plus a `summary`, so the generic protocol compaction
  truncated the body to a prefix and told the model to "use summary/artifacts" — wrong for
  a skill, whose full instructions are recovered only by calling `skill_view` again. The
  durable `skill_view` protocol message now keeps the prefix but swaps that marker for an
  explicit **reload hint** carrying the skill `name` + `base_dir` (+ `relative_file`), so a
  skill pruned from history stays recoverable across turns. Combined with the S1 catalog
  (re-injected every turn), a skill stays discoverable and reloadable across compaction too.
  The `skill_invocation` record is left untouched; other bulky tools keep the generic marker.

### Added

- **Runtime tool-scoping by a pinned skill (skills epic S6).** A skill's `allowed_tools`
  frontmatter was advisory only (safety warnings + a selection-time subset check). A host
  can now pin a run to a skill via `tool_policy.metadata["skill_scope"] = "<name>"`: the
  named skill is resolved from `skills_catalog_sources` and the model's visible tool surface
  is narrowed to that skill's declared `allowed_tools` — plus the skill-load tools
  (`skill_view`/`skill_tool`) so the model can still open the scoped skill. Enforced
  deterministically at the schema layer through the existing `llm_request_allowed_tools`
  narrowing (intersected with any deep-research narrowing, never expanding the surface), so
  it is host-controlled and predictable — NOT triggered by the model merely reading a skill
  (there is no "active skill" mode). No scope / unknown skill / a skill that declares no
  tools all leave the surface unchanged. `resolve_skill_allowed_tools(...)` is exported from
  `agent_driver.skills`.
- **Keyword-triggered skill surfacing (skills epic S5).** A skill can declare `keywords` in
  its frontmatter; when the user's request mentions any (whole-word, case-insensitive), the
  runtime injects a targeted hint naming that skill and how to load it — a proactive middle
  ground between the passive S1 catalog and full auto-injection (the body still loads on
  demand via `skill_view`, preserving progressive disclosure). This generalizes the
  previously hardcoded curated-research reminder into a config-driven, per-skill trigger.
  `build_skill_keyword_hints(...)` (exported from `agent_driver.skills`) scans the same
  `skills_catalog_sources` as the catalog (cached per run), dedupes, and caps at 3 hints;
  gated on a skill-load tool being available and re-injected into the system prompt each
  request (`skills_keyword_hints_block` metadata is diagnostic only). Path-glob and
  slash-command triggers are intentionally left to the consumer (repo-agent / UI concerns).
- **Tier-1 "available skills" catalog in the system prompt (skills epic S1).** Progressive
  disclosure's first tier — a compact catalog of `name + one-line summary + base_dir` — is
  now injected into the ReAct system prompt so the model *knows which skills exist* and can
  load a full body on demand via `skill_view`. Previously the renderer (`render_skill_entry`)
  existed but was unwired, so a general (non-research) agent got no signal any skill existed.
  Opt-in via new `RunnerConfig` knobs `skills_catalog_sources` (dirs to scan),
  `skills_catalog_max_chars` (budget; default 2000), and `skills_catalog_trusted_roots`.
  `build_skills_catalog_block(...)` (exported from `agent_driver.skills`) renders the block
  with graceful degradation — full entries → names-only → truncated `+N more` pointing at
  `skill_tool` — mirroring the reference frameworks' budget discipline. Accepts a `header`
  override so a localized consumer can supply its own intro. Gated on a skill-load
  tool (`skill_view`/`skill_tool`) actually being available, and re-built into the system
  prompt each request so it survives compaction by construction (`skills_catalog_block`
  metadata is diagnostic only). Empty sources = off; historical behaviour unchanged.
- **`SET_MAX_THINKING_TOKENS` control is now wired (steering epic A6).** Previously a
  recognized-but-unhandled `ControlKind` that hard-failed on drain, it now caps (or
  disables) the model's thinking/reasoning budget for subsequent LLM calls, mirroring
  `SET_MODEL`: the dispatcher writes `reasoning_max_tokens` into `tool_policy.metadata`,
  and request-build consumes it into the provider-neutral `LlmRequest.reasoning` envelope
  (positive int → `{"max_tokens": n}`, `0` → `{"enabled": False}`, unset → omitted so
  non-thinking backends are unaffected). Payload key `max_thinking_tokens` (alias
  `tokens`); a non-int/negative budget is reported `control_payload_invalid`.

### Changed

- **Honest reporting for recognized-but-unwired control kinds (steering epic A6).** The
  subagent controls `STOP_SUBAGENT` / `CONTINUE_SUBAGENT` — which the single-agent chat
  dispatcher cannot act on (it holds only the command queue, not a subagent store or child
  abort handle) — now drain to a distinct `control_kind_not_implemented` signal instead of
  being conflated with a genuinely unknown kind under `control_kind_unsupported`. Both
  still mark the item FAILED (never left QUEUED), but a host can now tell a feature gap
  from a version-mismatch/typo. The two cancellation seams these would need (run-level
  abort; child/subagent abort in the tool stage) are documented in
  `docs/live-message-controls.md`.

### Fixed

- **Priority-preemption drain — one canonical order across all backends (steering epic
  A5).** The step-boundary drain ranking (`_dispatch_order`) had been copy-pasted into
  the in-memory, SQLite, and Postgres command-queue stores and had already **drifted**:
  the `QUEUE_NEXT` rank branch existed only in the in-memory copy (the others reached the
  same number only by coincidence of NEXT-priority arithmetic), and INTERRUPT was matched
  by enum identity in one copy and by string value in the others. Consolidated into a
  single canonical `dispatch_order(item)` in `agent_driver.contracts.control` (now the one
  source of truth for NOW/preempt ordering: INTERRUPT → redirect → soft-steer → queued-
  next → everything-else-by-priority); all three stores import it. A cross-backend parity
  test locks memory and SQLite to identical drain order for a diverse control set so the
  copies can never silently diverge again.

### Added

- **Partial-output preservation on hard redirect (steering epic A4).** When a
  `REDIRECT_USER_MESSAGE` aborts an in-flight *streaming* LLM call, the text the model
  had already streamed before the abort — mirrored per-chunk into
  `assistant_stream_content` and surviving the task cancellation — is now folded into the
  re-ask as an assistant checkpoint turn ahead of the correction, instead of being
  replaced by a bare `[…прерван…]` placeholder. Text only (signed reasoning is streamed
  separately and never replayed, so role alternation stays valid); guarded on
  started-but-not-completed so a prior completed turn's content is never mis-attributed;
  the buffer is consumed after folding so a second abort in the same step cannot re-attach
  the same draft. The raw-free `steering_redirect_applied` signal now carries
  `partial_output_preserved` and `partial_output_chars` (count only, never the text).
- **Pause / resume — `PAUSE` steering control + `ResumeAction.CONTINUE` (steering epic A3).**
  A host can now park a running agent at the next step boundary without aborting it: the
  `PAUSE` control (`ControlKind.PAUSE`, resolving to the `pause_current` live-message
  semantic, applied at the next safe boundary) sets a transient
  `steering_pause_requested` marker that the LLM step consumes before its next call,
  synthesizing a `MANUAL_PAUSE` interrupt so the run returns as a resumable `PAUSED`
  output. Resume with a `ResumeCommand` carrying the new `ResumeAction.CONTINUE` — kept
  distinct from `APPROVE` so an audit never conflates a pause-resume with an approval
  grant — and the run continues from where it stopped (re-drives the pending LLM call).
  Boundary-only: an in-flight LLM/tool call always finishes before the pause takes effect
  (no mid-flight abort, unlike `REDIRECT_USER_MESSAGE`). See
  `docs/live-message-controls.md` and `docs/epics/steering/DESIGN-A3-pause-resume.md`.
- **Busy policy — `interrupt | queue | steer` message routing (steering epic).** A host
  now selects, per session, how a plain user message that arrives WHILE a run is executing
  is routed: `BusyPolicy.INTERRUPT` → hard redirect (abort + re-ask), `QUEUE` → hold for a
  fresh next turn, `STEER` → soft-steer fold into the current turn (no abort). One helper,
  `control_request_for_message(message, *, policy, …)`, translates "user typed while the
  agent was working" into the right `ControlKind` + priority; `parse_steering_text(…,
  busy_policy=…)` routes a bare (verb-less) message the same way (explicit stop/model verbs
  still win). All three shapes were already engine-supported; this names the choice.
- **Soft steer — `STEER_USER_MESSAGE` (steering epic).** A new live-control kind that
  folds user guidance into the CURRENT turn WITHOUT a new user turn and WITHOUT aborting
  the in-flight call: at the next step boundary it appends the guidance to the last
  tool-result message so it rides the pending LLM call as guidance on the work in progress
  (alternation-safe, ported from hermes-agent). This fills the gap between the existing
  `ENQUEUE_USER_MESSAGE` (new user turn at the boundary) and `REDIRECT_USER_MESSAGE` (hard
  mid-flight abort + re-ask, capped 2/step) — soft steer carries no abort tax and no
  redirect budget. Degrades to a normal user turn when there is no tool message to fold
  into (guidance is never dropped); idempotent by `queue_id`. Resolves to the
  `steer_current` semantic. See `docs/live-message-controls.md`.

- **Amortized rolling summary — Option B2 (compaction-improvement epic; horizon-scan
  047), opt-in.** When `enable_rolling_summary` is set, `llm_full` compaction folds the
  persisted prior summary + only the newly-overflowed message groups each firing
  (`rolling_summary` / `rolling_summary_covers_upto` cursor in run metadata) instead of
  re-summarising the full growing history from scratch — killing the ~12.5k redundant
  input tokens/step the Option-B measurement confirmed. The first firing degrades to a
  normal full compaction; subsequent firings fold only the slice past the cursor. Default
  **off**: a per-turn history rewrite breaks the provider prompt-cache prefix, so it is a
  trade, not a pure saving; `rolling_summary_every_n_turns` is the cadence dial. Ported
  from hermes-agent's micro-compaction, simplified by our immutable-log/ephemeral-View
  split (no DB-sync/resume machinery). See
  `docs/epics/compaction-improvement/DESIGN-optionB2-rolling-summary.md`.
  - **Phase 2:** `rolling_summary_every_n_turns` is now honored — with `> 1` the fold is
    deferred on non-cadence firings (`rolling_cadence_deferred`) so the prompt-cache prefix
    is rewritten every N firings instead of every one, never deferring under blocking
    pressure. A superseding `session_memory` compaction resets the rolling cursor so a
    stale rolling summary can't drop the richer session-memory content.

## [0.12.1] - 2026-08-07

### Fixed

- **Synthesis-only revisions now return directly to their owning finalize
  gate.** A corrected answer that contained a recommendation heading such as
  “Next step” could previously be intercepted by the generic continuation
  detector before the host quality gate reviewed it. That produced an
  unreviewed third model draft and could discard citations restored by the
  bounded revision. A `disable_tools=True` revision now bypasses generic
  continuation and node-contract reprompts on its return, is evaluated by the
  finalize hook, and either becomes the terminal answer or follows the gate's
  bounded fail-closed policy. The behavior of ordinary model turns and
  revisions with tools enabled is unchanged.

## [0.12.0] - 2026-08-07

### Added

- **Bounded fail-closed final-answer gates.** `RevisionRequest` can now request a
  synthesis-only revision (`disable_tools=True`), lower the runtime revision
  budget (`max_revisions`), and fail with typed `guardrail_blocked` semantics
  when the revised answer still violates a host-owned quality or safety rule
  (`fail_closed=True`). The no-tools boundary is enforced twice: tool schemas
  are hidden from the provider and the executor policy becomes `NO_TOOLS`.
  Runtime decisions expose `kind=final_answer`, `action=revise`, the gate id,
  bounded counters, and whether tools were disabled. Defaults preserve the
  historical fail-open rubric behavior.

- **Context occupancy telemetry (compaction-improvement epic; horizon-scan 047).** The
  token-pressure snapshot now reports `occupancy_pct` — the fraction of the compaction
  *trigger* the post-trim prompt fills, from the already-resolved (cached) threshold, no
  probe. It rides on every `llm_call_completed` as `context_occupancy_pct`, so a trace
  captures occupancy even on runs that never cross a pressure warning; the run-trace
  `context_pressure` summary aggregates it into `max_occupancy_pct` +
  `compaction_plane_dormant` (occupancy never approaches 1.0 and no compaction attempted
  ⇒ the LLM-compaction plane never engaged). This is the cheap, standalone first piece of
  the deferred B2 rolling-summary work — it measures whether the plane is dormant (the
  gating question) and reframes success off "tokens saved" toward flat occupancy. Additive
  and content-free.

## [0.11.0] - 2026-08-06

### Added

- **Compaction condenser pipeline foundation (compaction-improvement epic, Option B1a).**
  Internal `Condenser` / `CondenseContext` / `CondenseResult` / `CondenserPipeline`
  (`agent_driver.context.compaction.condenser`): a cost-ordered pipeline that runs
  reduction strategies cheapest-first and stops as soon as the request fits, with a
  `minimum_progress` anti-thrash floor and an honest `exhausted` outcome. Additive
  building blocks only — the live compaction dispatch is not yet wired onto them (B1b).

### Fixed

- **Compaction model sentinel no longer 400s (compaction-improvement epic).** A host
  that enabled `llm_compaction` without also configuring an `auxiliary_model` or an
  explicit `compaction_model` sent the field's `"default"` sentinel *literally* to the
  provider the first time compaction fired (`400 … "default" is not a valid model ID`),
  failing the compaction and tripping the circuit breaker. `_resolve_compaction_backend`
  now resolves the `"default"` sentinel (and an empty value) to the run's own model
  (`request.model`, which may be `None` → the provider's configured default), mirroring
  the primary completion. Found while A/B-measuring compaction on excel-ai's SSB
  workload, where enabling compaction is otherwise inert (single-turn tasks never reach
  the 0.85×window trigger). Regression:
  `tests/runtime/test_auxiliary_model_routing.py::test_default_compaction_model_sentinel_resolves_to_run_model`.

- **Compaction budget correctness (compaction-improvement epic, Option A phase 1).**
  - An unresolved model id (unknown/renamed/proxied) no longer silently assumes a
    12K context window — it falls back to a modern `UNRESOLVED_MODEL_CONTEXT_WINDOW`
    (128K) and emits a once-per-run `context_window_unresolved_fallback` warning so
    under-configuration is loud. A host-set `context_window_estimate` stays
    authoritative (set it explicitly for small local models to reproduce the old
    behaviour). *Minor* behaviour change for under-configured hosts.
  - **Data-loss fix:** LLM-full compaction unified its two protection predicates —
    a message flagged solely with `compaction_evidence` / `material_unit_hashes` was
    fed to the summariser and then silently dropped from the kept set; it is now
    retained verbatim (one `_is_protected_message` used by both the excerpt and the
    post-summary retention).
  - The summariser-input char cap is now a fraction of the resolved window's budget
    instead of a fixed 262144 ceiling that bound below the window on large-context
    models.
  - **(phase 1b)** The default (`runner_defaults`) path no longer clips the LLM-full
    compaction excerpt to ~4000 chars regardless of the model window: the compaction
    char budget now derives from the resolved window (kept separate from the
    deterministic-trimming `max_chars`, which is unchanged), so "full" compaction
    summarises history proportional to the real window.
  - **(follow-up)** The typed-budget path's compaction cap is now a fraction of the
    window char budget too (`COMPACTION_WINDOW_CHAR_FRACTION`, single-sourced with the
    compaction stage) instead of the fixed 262144 clamp — finishing BUG-1 on both
    paths.
  - **(BUG-6, phase 1)** The pressure trigger and budget conversions no longer assume a
    fixed 4 chars/token: the runtime now calibrates the ratio from each provider
    response's ACTUAL input-token count (a bounded EMA in `context_chars_per_token`,
    clamped to [2, 8]) and uses it for the next turn's estimate — so on CJK/RU/code
    content the compaction trigger fires closer to the real token count. Dependency-free
    and network-free; the first turn uses the 4.0 default until usage is observed. An
    optional pluggable `TokenCounter` and the display-only estimate sites remain phase 2.
  - **(BUG-3)** `compact_recommended` now has a window-relative floor
    (`TokenPressureInput.compact_ratio = 0.75`), like the other pressure states —
    previously it was the only state with no ratio net, so compaction fired at
    0.75·window on the default path but ~0.90·input_tokens on the typed path. Both
    paths now compact at ~0.75·window (default path unchanged; typed path earlier and
    consistent). The absolute-threshold formulas are intentionally left per-path (the
    ratio nets govern behaviour).

### Changed

- **Internal: consolidated the secret-field redaction validators.** The
  `reject_secret_like_keys` / `is_sensitive_key` / `looks_like_env_name` /
  `assert_no_secret_fields` helpers (and the secret-marker list) now live once in
  `agent_driver.contracts.validation`; nine contract modules
  (`execution`, `execution_lease`, `execution_job`, `harness_adapter`,
  `continuous_validation`, `capability_packs`, `lifecycle_hooks`,
  `durable_lifecycle`, `provider_catalog`, `mcp_governance`, `skills_lifecycle`)
  that had each carried a copy now call the shared helpers. Behavior-preserving
  (the secret-value regex and raw-content guards are threaded through unchanged);
  no public surface change. Removed the private cross-module import of
  `_reject_secret_like_keys`.
- **Internal: removed proven-dead private helpers** (unused thin wrappers
  `_gemma_tool_call_payloads` / `_dsml_tool_call_payloads` / `_resolve_args_with_config`
  and the orphan `_is_sequence_of_str`). No behavior or surface change.
- **Internal: de-tangled the interactive chat CLI.** `_handle_local_command`'s
  264-line 24-branch if/chain became one small handler per slash command plus a
  dispatch table (`_ChatCommandContext` bundles the shared pass-throughs); the
  `render_chat_stream` render loop lost its two most tangled inline blocks to the
  `_run_failure_hint` / `_completed_tool_card` helpers. Behavior-preserving; the
  `agent_driver.cli` surface is unchanged.
- **Internal: safe extractions in the `compaction_stage` god-functions**
  (behaviour-preserving, self-contained sub-blocks only — no control-flow change):
  `_apply_llm_full_compaction` (222 lines) lifts its ~40-line excerpt builder to a
  pure `_build_full_compaction_excerpt` (returning a small dataclass unpacked back
  into the same locals, so downstream code is byte-identical) and its provider/model
  resolution to `_resolve_compaction_backend`; `apply_compaction_if_eligible` lifts
  its ineligible-skip block to `_finalize_ineligible_compaction`.
- **Internal: extracted `research_signals` from `research_session_contract`
  (1086 lines → 620).** This module has no clean core/report seam (its contract
  builders and gating predicates are mutually recursive), so instead the
  call-graph-verified pure leaf — the deep-research signal vocabulary (phase /
  readiness / tool-set constants) plus the 30 context/gating predicate functions
  that read signals from a run context, task contract, and tool state — was moved
  to `runtime/research_signals.py`. The contract dataclasses and builders depend on
  it one-directionally; every existing import and the `__all__` are preserved.
- **Internal: split the `skills/lifecycle` god-module (1191 lines → 80).**
  Continuing the earlier `lifecycle_common` / `lifecycle_evidence` extraction, the
  remaining bulk splits into `lifecycle_inventory` (skill id/inventory/lock/diff +
  selection decisions — 24 symbols) and `lifecycle_report` (compatibility report,
  usage summary, hook/adapter projections, support-bundle projection, seeds — 15),
  call-graph-verified one-directional (report→inventory, no cycle). `lifecycle` is
  now a thin re-export shim preserving every existing import and its `__all__`.
- **Internal: split the `mcp_server/governance` god-module (1030 lines → 72).**
  Behaviour-neutral split into `governance_core` (catalog, registry, approval,
  provenance, usage, policies — 20 functions) and `governance_report` (support-bundle
  projection, compatibility report, evidence index, markdown, artifact write/replay,
  deterministic seeds — 15 functions), verified one-directional (report→core, no
  cycle). `governance` is now a thin re-export shim, so every existing
  `agent_driver.mcp_server.governance` import (and its `__all__`) is unchanged.
- **Internal: consolidated the observer-redaction and artifact-manifest dups.**
  The three copies of the recursive `_redact_value` + base_url-aware
  `_is_sensitive_key` (support_bundle / provenance / stream projection) collapse to
  `redact_sensitive_values` + `is_sensitive_observer_key` in
  `agent_driver.observability.redaction` (provenance's raw-content drop is threaded
  through `drop_keys`); the three `_artifact_row` sha256 manifest-row builders
  (durable-lifecycle report / adapter protocol / validation artifacts) collapse to a
  shared `artifact_manifest_row(..., include_id=…)`, so the writer and the
  policy-supervision verifier can't drift on hash algorithm. Behavior-preserving.
- **Internal: safe extractions in three runtime hot-path functions**
  (behavior-preserving, self-contained sub-blocks only): the LLM completion
  retry loop's seven repeated WARNING emits collapse to `_emit_provider_retry_warning`;
  `execute_allowed_path` lifts its progress-recorder and cancellation setup to
  `_make_progress_recorder` / `_build_cancellation`; and deterministic trimming
  lifts the tool-stub builder and the protected-message reason logic to
  `_tool_trim_stub` / `_protected_keep_reason`. No control-flow or behavior change.
- **Internal: decomposed the runtime hot-path god-functions** (behavior-preserving;
  extract failure/success/dispatch segments into named helpers, leave shared-state
  loops inline). Eleven functions: lifecycle `_execute_finalize` (222→80), executor
  `_execute_one_call` (205→111) and `execute_allowed_path` (216→100); the compaction
  stage `apply_compaction_if_eligible`, `_apply_llm_full_compaction`, and
  `_apply_session_memory_compaction`; llm_step `execute_llm_call_step` (390→245),
  `build_single_agent_llm_request` (266→119), `complete_request` (229→108, dropped the
  `too-many-branches` disable), and `retry_forced_final_without_tools` (240→171); the
  run-trace `summarize_run_trace` (308→202); and deterministic `_trim_messages_to_budget`
  (safe subset only). No control-flow or behavior change; full suite unchanged (3249).

## [0.10.0] - 2026-08-05

### Added

- **Sequential evidence-led tool-call control.**
  `AgentRunInput.max_tool_calls_per_step` and
  `RunnerConfig.default_max_tool_calls_per_step` add an opt-in cap on calls
  accepted from one model response. A limit of one sends
  `parallel_tool_calls=false` to OpenAI-compatible providers and is also
  enforced inside the runtime before approval or execution. Provider-returned
  calls above the limit are suppressed with the redaction-safe
  `planned_tool_call_step_limit_applied` diagnostic, so the next reasoning step
  observes real results before choosing more work. Defaults remain unchanged.

## [0.9.0] - 2026-08-05

### Added

- **Backend compatibility kit & release surface (execution-backend EPIC-05,
  final epic of the package).** Backend authors can implement the public
  protocol and prove exactly which guarantees their adapter provides, from public
  docs alone.
  - Compliance contracts (`agent_driver.contracts.execution_compliance`,
    re-exported from `agent_driver.execution`): `ComplianceReport` /
    `ComplianceCheck` / `ComplianceStatus` (passed/failed/unsupported/skipped/
    stale/no_claim) / `ComplianceGroup` — versioned, bounded, redaction-safe.
  - `run_compliance(backend)` runs the deterministic compatibility suite against
    ANY backend with no live LLM / Docker / network / credentials: each group
    runs only when the backend advertises the matching capability, an
    unadvertised group is `no_claim` (never inflated to a pass), and a
    guarantee that is advertised but not proved (e.g. hard teardown only
    acknowledged) is a `failed`. `render_markdown` emits a concise report.
  - `examples/cookbook/21_backend_compliance.py`: a minimal third-party-style
    backend implemented with ONLY public imports, qualified by the suite. The
    built-in `LocalExecutionBackend` qualifies truthfully (command + identity
    proved; remote-lifecycle groups `no_claim`).
  - `docs/execution-backend-migration.md`: migrating legacy
    `AsyncCommandRunner`/`AsyncFileIO` hosts to an injected `ExecutionBackend`
    (via `CompositeExecutionBackend`) within the pre-1.0 deprecation window.

## [0.8.0] - 2026-08-05

### Added

- **Reconnectable execution jobs, events, control & teardown (execution-backend
  EPIC-04).** A long-running backend operation that can outlive a transport
  connection now has a stable identity, bounded ordered events, reconnectable
  snapshots, truthful controls, generation fencing, and a separate teardown
  proof — a lost HTTP/WebSocket/process reply never makes Agent Driver blindly
  repeat an unknown side effect.
  - Job contracts (`agent_driver.contracts.execution_job`, re-exported from
    `agent_driver.execution`): `ExecutionHandle` (safe, durable,
    generation-bound; carries the start idempotency key), `ExecutionEvent`
    (identity `(execution_generation, sequence)`, bounded text, secret-rejecting
    metadata, conflict detection), `ExecutionEventCursor`/`Page` (bounded,
    gap-flagged replay), `ExecutionTerminalSnapshot`, `ExecutionControlRequest`/
    `Receipt` (accepted vs applied are distinct), `TeardownReceipt`, and the
    `ExecutionJobState`/`ExecutionEventKind`/`ExecutionControlKind`/
    `ExecutionReasonCode` enums.
  - Optional `JobCapableBackend` protocol (`start_job`/`lookup_job`/`observe`/
    `snapshot`/`control`/`teardown`) — a backend without it still runs the
    blocking `run_command`.
  - `JobObserver` (duplicate-tolerant, generation-fenced event accumulation;
    gap → snapshot; conflicting terminal → `TerminalConflictError`) and
    `JobSession` (idempotent + lost-start-safe start → `lookup` → INDETERMINATE
    without re-dispatch; observe-to-terminal resilient to transport loss;
    per-phase `JobStageTiming` with typed reason codes). `stop_job` /
    `JobStopOutcome` report accepted/applied/execution-terminal/teardown-confirmed
    as SEPARATE, capability-backed facts (a cooperative-only backend claims
    nothing more; a host-owned environment is never torn down). Durable recovery
    via `persist_job_recovery`/`restore_job_recovery`.
  - Runtime bridge: tool progress (and, through it, a job's bounded observed
    events) now reaches the runtime event log / stream projection as
    `RuntimeEventType.TOOL_PROGRESS`, correlated to the originating tool_call_id.

## [0.7.0] - 2026-08-05

### Added

- **Execution leases & complete workspace routing (execution-backend EPIC-03).**
  A task-scoped, generation-bound lease lets one prepared backend workspace be
  acquired/attached once, reused across the whole agent loop, and released
  (runtime-owned) or detached (host-owned) on every exit — while every built-in
  filesystem operation routes to that workspace with backend-relative path
  safety. The external backend owns infrastructure; Agent Driver owns correct
  use of the lease inside the run.
  - Lease contracts (`agent_driver.contracts.execution_lease`, re-exported from
    `agent_driver.execution`): `ExecutionLeaseRequest`, `ExecutionLeaseRef` (the
    only durable, non-secret, generation-bound reference), `ExecutionLease`,
    `LeaseReceipt`, `WorkspacePaths`, ownership/state/phase enums,
    `EXECUTION_LEASE_SCHEMA_VERSION`. Optional `LeaseCapableBackend` protocol and
    an `ExecutionLeaseManager` (idempotent acquire/attach/reuse; release/detach
    exactly once; fail-closed to `LeaseNotUsableError`/`UnsupportedCapabilityError`,
    never a silent local fallback).
  - Runner integration: `RunnerConfig.execution_lease_ownership` (or a host-
    supplied attach ref in `app_metadata["execution_lease_ref"]`); acquire/attach
    once, reuse across steps, persist the safe ref for resume, and release in the
    authoritative outer `finally`. A PAUSED run retains its lease; a subagent
    child never acquires or releases the parent's lease (default ISOLATE policy).
  - Complete workspace routing: `WorkspaceCapableBackend` (list/glob/grep/stat/
    delete) plus routing-aware path resolution — when a backend is active,
    `read`/`write`/`edit`/`patch`/`glob`/`grep`/`artifact_*`/`notebook_edit`
    validate paths lexically against the lease `WorkspacePaths` (no local stat,
    no local fallback) via `validate_workspace_path`, and route bytes/enumeration
    to the backend. Filesystem builtins declare FILE_READ/FILE_WRITE execution
    requirements (capability-gated).
  - Artifact bridge: a backend-produced content-addressed `ArtifactRef`
    (digest + size) is surfaced as a bounded, model-facing reference
    (`execution_artifact_reference_payload`) and mapped to the context artifact
    vocabulary (`execution_artifact_to_context_ref`) — only a bounded preview
    enters model context, never an implicit full-content load.
  - Per-phase lease timings are surfaced to `metadata["execution_lease_receipts"]`.

## [0.6.0] - 2026-08-05

### Added

- **Execution capabilities & safe environment routing (execution-backend
  EPIC-02).** A host-injected backend can now report truthful, revisioned
  capabilities; Agent Driver enforces them above and below the model and shows
  the model a bounded environment brief. The host stays the only authority that
  selects the backend.
  - New capability contracts (in `agent_driver.contracts.execution`, re-exported
    from `agent_driver.execution`): `ExecutionCapabilitySnapshot` (typed
    `CapabilityName` → `CapabilityStatus` map, bounded `ProgramInfo` inventory,
    `environment_revision`/`lease_generation`/`digest`, secret-rejecting
    `metadata`), `ToolExecutionRequirement`, `RequirementCheck`,
    `EnvironmentBrief`, and `EXECUTION_CAPABILITY_SCHEMA_VERSION`. The reserved
    minimal `CapabilitySnapshot` from 0.5.0 is replaced by this fuller model.
  - Optional `CapabilityAwareBackend` protocol adds `async capabilities()`;
    `LocalExecutionBackend`/`FakeExecutionBackend`/`CompositeExecutionBackend`
    report truthfully. A backend that does not implement it is treated as
    all-`UNKNOWN` (hard requirements then fail closed).
  - Routing helpers `resolve_capability_snapshot` (fail-safe to all-`UNKNOWN`),
    `check_requirement`/`check_manifest_requirement`/`tool_is_withheld`,
    `derive_environment_brief`, `render_environment_brief_text`,
    `capability_diagnostics`.
  - `ToolManifest.execution_requirement` (host/registry data, never a model
    argument): a tool with an unmet HARD requirement is withheld from the model
    schema (pre-model) and denied before dispatch (pre-dispatch, anti-TOCTOU,
    typed `capability_unmet`). The runner performs one capability handshake per
    run and exposes it to both checks via a run-scoped snapshot.
  - The deterministic environment brief is injected as request-only context
    (never persisted); a redaction-safe `capability_audit` rides request
    metadata. Default runs with no backend / no requirements are unchanged.

## [0.5.0] - 2026-08-05

### Added

- **Public `ExecutionBackend` seam (execution-backend EPIC-01).** A new supported
  `agent_driver.execution` facade lets a host route the built-in `bash`/`read`/
  `write` byte transfer through an injected backend (a prepared local or, later,
  remote workspace) without changing the agent loop or governance order — the
  model can never select the backend.
  - Validated, JSON-safe contracts (`agent_driver.contracts.execution`, re-exported
    from the facade): `ExecutionIdentity`, `ExecutionBounds`, `ArtifactRef`,
    command/read/write request+result models, a reserved `CapabilitySnapshot`, and
    `EXECUTION_SCHEMA_VERSION`. Results are typed, never a raw `dict`.
  - A runtime-checkable async `ExecutionBackend` protocol (minimal command +
    text read/write surface; lease/capability/event/control vocabulary reserved
    for later epics).
  - Typed, bounded, redaction-safe `ExecutionError` hierarchy, categorizable by
    type/`code` without parsing messages.
  - `LocalExecutionBackend` (faithful subprocess + local-disk reference),
    `FakeExecutionBackend` (deterministic, for tests), and
    `CompositeExecutionBackend` (bridges a legacy `AsyncCommandRunner`/`AsyncFileIO`
    pair; the supported ACP shim primitive).
  - Injection via `RunnerConfig.execution_backend` and a per-run
    `Agent.run(execution_backend=...)` / `SingleAgentRunner.run(execution_backend=...)`
    override; the executor propagates full run/attempt/tool-call/request identity
    to the backend only after governance allows the call.
  - Default local behavior is unchanged when no backend is configured. ACP keeps
    its current terminal/file routing; its full cutover to the seam is EPIC-02.

## [0.4.1] - 2026-08-05

Patch release fixing executor-owned tool-call identity inside host handlers.
The governed allow path now places the exact `ToolCall.tool_call_id` and its
per-call execution `attempt_id` into the existing run-scoped tool context,
alongside `run_id` and `thread_id`. This lets embedding hosts durably correlate
a handler's product action, approval, execution, evidence, and terminal result
with the same call that appears in Agent Driver events, instead of inventing a
fallback identity.

The change is backward compatible: handler arguments, tool/result schemas,
public facades, stores, and provider contracts are unchanged; the existing
context setters only gain optional identity values and still reset after each
handler. A regression test covers exact propagation and scope cleanup.

## [0.4.0] - 2026-08-04

Minor release adding the public live-message contract v1 for host embeddings.
`ENQUEUE_USER_MESSAGE/NOW` is now a non-aborting current-turn soft steer,
`REDIRECT_USER_MESSAGE/NOW` advances a durable LLM generation and redirects
only an in-flight model await (degrading visibly during tool/approval phases),
and `ENQUEUE_USER_MESSAGE/NEXT` remains pending until a distinct post-terminal
turn. Pending NEXT messages are cancellable; Stop remains a separate durable
preemption boundary.

The generic command receipt now records explicit requested/resolved semantics,
FIFO sequence, phase, `applies_at`, timestamps, stable reason codes, content and
request hashes, LLM generations, claim identity, and NEXT handoff/destination
identity. Typed accepted/applied/cancelled/failed/redirected/promoted/
Stop-preempted/handoff events use the raw-message-free receipt projection.
Hard redirect fences late old-generation streaming and non-streaming output
before transcript, checkpoint, tool, or terminal mutation.

Postgres adds additive `live_message_runs` and `control_schema_meta` state and
serializes semantic mutations across processes. Ambiguous legacy NEXT rows are
quarantined as `legacy_unresolved`; mixed runtime/schema generations fail
closed. In-memory and SQLite implement the same reference state machine for
tests/diagnostics. `dispatch_next_turn()` supplies the stable host seam for an
idempotent one-turn/one-message handoff across claim/create/append/readback
crashes.

New supported symbols are exported from `agent_driver.contracts`,
`agent_driver.runtime`, and `agent_driver.embedding`; the SDK adds `steer`,
`redirect`, `queue_next`, `cancel_next`, `stop`, and typed readback. See
`docs/live-message-controls.md` and `docs/live-message-migration.md`.

## [0.3.3] - 2026-08-03

Context-integrity patch release for constrained, long-context embeddings.
Leading system contracts and current turns now survive message-count trimming;
messages explicitly marked for compaction remain atomic with a deterministic
size-only audit when their semantic protection exceeds a route limit. Structured
JSON tool results are replaced by an unambiguous stub instead of a malformed raw
slice, and structured tool arguments remain atomic. Token pressure now includes
message metadata/tool calls and the surfaced tool catalogue, and the public
context breakdown uses the same accounting.

Adds the supported `RunContextBudget` field on `AgentRunInput`, the
`resolve_run_context_budget` facade, a bounded full-compaction packet, and one
deprecation window for `app_metadata.context_budget`. Adds
`serialize_runtime_state_for_compatibility(..., target="0.2.0rc5")` for rolling
rollback without a database reset; its audit contains paths and strategies, not
raw messages, evidence, tool payloads, or reasoning. The package now declares
the explicit SPDX-compatible reference `LicenseRef-NOASSERTION`; see
`docs/licensing.md`.

### Fixed — blind-retry transient connection/transport errors in the LLM step
The completion retry loop already retried transient HTTP statuses (429/5xx) and timeouts, but a
connection/transport blip fell through to a bare `raise` and failed the whole run. `httpx.ConnectError`
("All connection attempts failed"), `httpx.RemoteProtocolError` ("Server disconnected"), the
sibling-teardown `ReadError`, and the wrapping `ProviderTransportError` are now blind-retried up to twice
with a bounded backoff (`transient_transport_retries` in run metadata; a `provider_transient_transport_retry`
warning event), mirroring the existing transient-status path. `LocalProtocolError` (a client-side body bug)
is deliberately excluded. Surfaced while running excel-ai's SpreadsheetBench on OpenRouter, where ~14 of
200 tasks failed purely on transient network hiccups that a retry recovers. Tests:
`test_complete_request_retries_transient_transport_error`,
`test_complete_request_gives_up_transport_error_after_bounded_retries`.

## [0.3.2] - 2026-08-03

Patch release replacing the non-reproducible `0.3.1` artifact identity. The
`0.3.1` wheel build inherited the checkout permissions of
`scripts/check_package_layout.py`, so otherwise identical source trees could
produce wheels whose ZIP metadata recorded `100777`, `100775`, or `100755` and
therefore had different SHA-256 values. Runtime payloads were identical, but
the byte-for-byte reproducibility claim was false.

`make release-wheel` now builds exclusively from a clean committed
`git archive`, normalizes every tracked regular file to its Git executable bit
(`0644` or `0755`), sets a fixed process umask and locale, and derives
`SOURCE_DATE_EPOCH` from the exact commit. CI invokes this builder twice under
different caller umasks, requires byte-identical wheels, verifies metadata and
imports, and publishes the selected wheel as a run artifact.

The patch also adds two additive embedding seams needed by downstream hosts:
`agent_driver.runtime.runner_config_parameter_names` replaces imports of
private flattened-settings field sets, and
`agent_driver.tools.register_skill_tools` supports intentionally narrow tool
registries. `agent_driver.runtime.RevisionRequest` and the contracts
`AllowedPrompt`/`AllowedPromptPattern` are also promoted to their owning
facades so lifecycle and HITL host adapters do not import implementation
modules. `MemoryStep`/`MemoryStepKind` likewise move to the contracts facade
for host memory projections. All are re-exported by
`agent_driver.embedding`; persisted-state contracts and existing runtime
behavior are unchanged from `0.3.1`.

## [0.3.1] - 2026-08-03

Follow-up to the `0.3.0` remediation, answering the downstream verifier's two findings against the DoD:
`make lint` failed on isort import-ordering in five subagents files, and there was no type/docs check or
supported-Python-matrix proof. This release makes `make lint` green, adds a real **type gate**
(`make type` / `pyrightconfig.json`, scoped to the supported embedding surface + durable control plane)
and a **docs-consistency gate** (`make docs-check`), and wires lint + type + docs + a Python **3.11/3.12
matrix** into CI (`.github/workflows/tests.yml`). Because this changes the source tree (and therefore the
wheel), `0.3.1` is a fresh release identity — `0.3.0`'s wheel/SHA is not reused. No public API change vs
`0.3.0`. The OpenRouter credit-402 fix (below), previously carried on a separate branch and excluded from
`0.3.0`, is merged into `main` and shipped transparently here.

### Fixed — isort import-ordering + type/docs/Python-matrix DoD gates
`isort` reordered imports in `agent_driver/subagents/{mailbox,executor,child_helpers}.py` and two subagent
test files so `make lint` (ruff + isort/black + pylint ≥ 8.0) is green. Added `...` bodies to the
`CommandQueueStore` and `PlanArtifactStore` Protocol method stubs so the type gate is clean. New
`pyrightconfig.json` type-checks the supported surface (embedding facade, durable control stores, tool-gate
contracts, plan artifacts) at 0 errors — the repo is not yet under a repo-wide type gate (pre-existing
findings in internal modules are tracked separately). `make type` and `make docs-check` targets added;
`ruff` + `pyright` added to the `dev` extra. CI now runs the default suite and `make lint` across Python
3.11 + 3.12, plus dedicated `type` and `docs` jobs, alongside the mandatory `postgres-suite`.

### Fixed — credit-402 recovery clamps to the provider-stated affordable budget
The OpenRouter credit `402` states the exact output budget a near-empty balance can support
(`"...but can only afford 298"`). The `reduced_after_provider_402` retry ladder previously halved
`max_tokens` with a hard **512 floor** — so when the affordable ceiling was below 512, every reduced
retry kept 402-ing and the run hard-failed with a bare `LLM completion failed` (surfaced while running
excel-ai's SSB benchmark on a nearly-depleted OpenRouter key: the model, tools, and reasoning path all
worked, but the harness could not degrade to a best-effort answer). `affordable_max_tokens_from_error`
now parses the stated ceiling (min across the body's current + `previous_errors` figures) and
`request_with_reduced_max_tokens(request, affordable)` clamps to just under it (10% margin), **below the
512 floor when the provider is that constrained** — the stated figure is authoritative. A ceiling below
`_MIN_AFFORDABLE_MAX_TOKENS` (64, too small for even a minimal tool call / one-line final) is treated as
a genuinely depleted balance: no change, so the 402 propagates as a clear error instead of looping. The
generic (no-number) 402 path is unchanged (halve, 512 floor). Domain-neutral runtime robustness — no
excel-ai change. Test: `test_credit_error_clamps_below_floor_to_stated_affordable_budget`.

## [0.3.0] - 2026-08-03

Remediation release answering the PentestLens `UPSTREAM_REMEDIATION_REQUEST` (R0–R6). Re-cut from a single
clean source SHA that contains **all** the work claimed complete — including the U1 `agent_driver.embedding`
namespace and the U4 bounded-cancellation-deadline wiring that landed *after* the `0.2.0` wheel was built —
plus new durable Postgres control stores (R2), full ToolGate provenance projection (R1), plan-binding trace
projection (R3), and the completed U4 Stop matrix (R4). The approved input contract was restored to its
authoritative SHA (R0) and statuses reconciled (R6). `0.2.0` identity/wheel is **not** reused. Every change
is additive and persisted-state compatible.

### Added — U4 Stop matrix completion + release-artifact verification (epic 060 / R4)
Fills the last uncovered cells of the U4 abort/cancellation matrix and documents the full cell→test
mapping (epic 060): a pre-aborted planning run now has a test proving it terminates `CANCELLED` without
starting the plan step (no new transition after an observed abort), and a test proving a fenced late
result (straggler from a superseded attempt) is ignored and never reopens the run. All U4 code — including
the bounded cancellation deadline wiring — is confirmed present in the release source tree (unlike `0.2.0`,
whose wheel predated the deadline commit), so the 0.3.0 wheel actually carries the declared behaviour.
Test: `tests/runtime/test_u4_stop_matrix.py` (2).

### Fixed — plan policy binding reaches the checkpoint/resume/trace projection (epic 059 / R3)
The approved-plan `PLAN_APPROVED` / `PLAN_REJECTED` trace event now carries the host-authored
`policy_binding` and `approved_by` alongside `plan_id` and `content_hash`, so a host can prove — after
compaction / reconnect / resume — that the executed plan is the exact version it bound to an
authorization/policy snapshot. Previously the binding lived only in live `context.metadata` and never
reached the runtime event a host reads. The lifecycle payload is now built from the harness-authoritative
`approved_plan` (computed before the event is emitted), which fixes a latent bug: on an **EDIT** the trace
event carried the *stale pending* `content_hash` instead of the re-hash of the edited content — a host
enforcing re-approval against the traced hash would have compared against the wrong plan. The binding is
sourced only from the resume command / authoritative approved plan, never model/tool output, so it stays
unforgeable, and it survives a real checkpoint round-trip (`RuntimeState.metadata`). Closes R3.
Tests: `tests/runtime/test_plan_binding_trace.py` (4 — trace event carries the binding, real checkpoint
readback, unforgeable without resume metadata, EDIT re-hash on the trace event).

### Added — ToolGate provenance reaches the terminal / trace projection (epic 057 / R1)
Gate decision provenance now flows all the way to the trace-safe `runtime_decision` projection a host
reads, not just the tool policy outcome and the ask interrupt. The runtime stamps a reserved
`_ad_gate_decision` marker (`"allow" | "deny" | "ask"`) alongside the existing `_ad_gate_provenance`
whenever a **gate** produced the decision; both ride the executed `ToolResultEnvelope.metadata` for
allow/deny/ask (previously only the ask interrupt carried them), and the tool-stage projection folds them
into `RuntimeDecision.redacted_metadata`. A gate denial is now told apart from a static policy denial in
that projection — `policy_id="tool_gate"` / `trigger="tool_gate_denied"` vs `"tool_policy"` /
`"tool_denied"` — without pattern-matching reason strings, and call identity (`tool_call_id`) is stable
from the gate context through to the terminal decision. Reserved keys stay under the `_ad_` namespace so
model/tool metadata can neither forge nor overwrite host provenance; malformed provenance still fails
closed (DENY) end-to-end. Closes R1 of the remediation Goal. New export: `RESERVED_GATE_DECISION_KEY`.
Tests: `tests/runtime/test_provenance_lifecycle.py` (7 — envelope provenance for allow/deny/ask, terminal
projection with gate≠static distinction, redaction-safe metadata, stable identity, forged-key fail-closed).

### Added — full Postgres-backed durable control plane (epic 058 / R2)
Production-durable Postgres implementations of all four generic control-plane stores —
`PostgresApprovalConsumptionStore`, `PostgresAbortLifecycleStore`, `PostgresPlanArtifactStore`,
`PostgresCommandQueueStore` — plus a shared `PostgresControlStoreConfig`, all re-exported from the
supported `agent_driver.runtime` facade (and in the export snapshot). They live in one **generic** schema
(default `agent_driver_control`) with their own idempotent DDL; nothing shares a transaction with product
tables and nothing carries product semantics. Exactly-once approval consumption is an `INSERT … ON
CONFLICT DO NOTHING` compare-and-swap (untargeted, so it covers the primary key and the partial
idempotency-key unique index, mirroring SQLite's `INSERT OR IGNORE`); the abort lifecycle advances through
a monotonic `ON CONFLICT … DO UPDATE` CAS that never moves a terminal row backwards. Closes R2 of the
remediation Goal — SQLite/in-memory proved the algorithm; this proves the transactional/unique/CAS
semantics of the real backend a multi-worker product coordinates through. psycopg (v3) is imported lazily
(`agent-driver[postgres]`), so nothing changes for hosts that do not use Postgres. A new **mandatory**
CI job (`.github/workflows/tests.yml`) runs the real-Postgres acceptance matrix against a pinned
`postgres:15-alpine` with `AD_REQUIRE_POSTGRES=1`; the `postgres` pytest marker is excluded from the
default sweep, and a missing DSN / dependency / zero collected tests **fails** the job rather than skipping
green. Tests: `tests/runtime/test_postgres_control_plane.py` (17 — two-client race single-winner,
crash-safe replay, conflict, monotonic abort CAS, restart readback, cross-backend parity) +
`tests/runtime/test_postgres_resume_integration.py` (3 — the Postgres approval store wired into the real
runner resume path: a duplicate resume conflicts before the tool runs twice, two concurrent resumes
produce exactly one side-effect, and a stale `expected_checkpoint_id` conflicts before anything is
consumed). 20 real-Postgres tests green against `postgres:15-alpine`.

### Added — bounded cancellation deadline on the tool cancellation token (epic 052 / U4)
`ToolCancellation.deadline_seconds` — previously always `None` — is now populated from the run's
`deadline_seconds`, so a cooperative handler consulting `current_tool_cancellation()` sees the outer
time bound within which it should wrap up its own cancellation (socket/browser/query). Sourced at the
handler-invocation site from `AgentRunInput.deadline_seconds` (no new config, no run-metadata
pollution); `None` when the run set no deadline. Fills the last U4 cancellation-hook DoD field. Full
sweep 2969 passed (+1).

### Added — single `agent_driver.embedding` aggregate namespace (epic 049 / U1, phase E)
One import root re-exporting the embedding-essential names from the per-concern facades
(`sdk`/`runtime`/`llm`/`contracts`/`tools`) — so a host can `from agent_driver.embedding import
create_agent, RunnerConfig, SqliteRuntimeStore, ToolGateAsk, RunAbortHandle` instead of tracking which
facade owns each name. Every name is an **identity** re-export of the same object on its owning facade
(a test asserts this, so the aggregate can never drift), and it adds no new API — the per-concern
facades remain the full surface. Documented as the first row of the `docs/embedding.md` table. This
closes the last U1 item; U1 (supported embedding facade) is now complete.

## [0.2.0] - 2026-08-02

Release candidate for the PentestLens embedding Goal (epics 048–055 / U1–U7): a supported embedding
facade plus durable, domain-neutral control contracts — tool-gate call identity + provenance (U2),
atomic exactly-once approval consumption with prior-result replay (U3), a durable stop / host
cancellation lifecycle with result fencing (U4), plan-integrity hashing + enforcement (U5), a truthful
non-durable Gateway declaration (U6), and a coherent `__version__`/release handoff (U7). Every change
below is **additive and persisted-state compatible** — new optional contract fields default to
`None`/absent, new metadata keys are additive, existing checkpoints/events remain readable, and no
public symbol was removed or renamed.

### Added — embedding e2e example + cookbook off internal paths (epic 049 / U1, phase D)
New `examples/cookbook/19_embedded_e2e.py` assembles a full durable embedding from the supported
facades only (`agent_driver.sdk`/`.runtime`/`.llm`/`.contracts`): a fake provider, host-owned
checkpoint + event stores, a custom governed tool (`agent.add_tool`), a lifecycle hook, an approval
tool-gate, and a run abort handle — exercising the whole pause → approve → resume path plus a durable
abort, with no `runtime.single_agent.*`/underscore import. It is covered by the cookbook smoke test.
The two cookbook examples that reached past the facade into `agent_driver.runtime.tool_gate` now import
`ToolGate*` from the `agent_driver.runtime` facade root. Full sweep 2965 passed (+1). This is U1 phase
D; the remaining U1 item is a single aggregate `agent_driver.embedding` namespace (optional).

### Added — embedding facade completed for durable-host primitives (epic 049 / U1)
The `agent_driver.runtime` facade now re-exports the categories an embedder previously had to reach
into runtime submodules for: the **host-store protocols** (`CheckpointStore`, `RuntimeEventLog`,
`CheckpointRecord`, `StorageCapabilities`), the **durable command/control store** family
(`CommandQueueStore`, `InMemoryCommandQueueStore`, `SqliteCommandQueueStore`), the **lifecycle-hook
protocol** (`RunLifecycleHook`, `BaseRunLifecycleHook`), and the **run/stream projections**
(`project_runtime_events`, `project_run_timeline`, `backfill_stream_events`, `summarize_run_lifecycle`,
`RunLifecycleSnapshot`, `RunLifecycleState`, `RunTimelineRow`, `RuntimeSessionDiagnostics`). A host can
now build a durable embedding (host stores + lifecycle hook + stream projections) importing only from
`agent_driver.runtime`, never `runtime.storage` / `.control` / `.lifecycle_hooks` / `.stream`. The
`docs/embedding.md` table and the `test_public_exports` guard were updated to lock the surface; the
tools-vs-runtime boundary (no `ToolRegistry`/`GovernedToolExecutor` on `runtime`) is preserved.
Additive; full sweep 2907 passed. Remaining U1 (epic 049): a single `agent_driver.embedding`
aggregate namespace, an exact (equality) export snapshot + deprecation policy, and repointing the
cookbook examples off internal paths onto the supported facade.

### Added — runtime `__version__`, single-sourced from package metadata (epic 055 / U7)
`agent_driver.__version__` now exists, resolved from the installed distribution metadata
(`importlib.metadata`) with a pyproject-matching fallback for a bare source tree. A guard test
(`tests/test_version.py`) asserts it agrees with `pyproject.toml` `[project] version` and is a valid
pre-1.0 version, so the runtime version, package metadata, and the eventual wheel cannot silently
drift. Remaining U7 (release handoff, gated on the full Goal passing): select the next valid pre-1.0
version, a clean deterministic wheel build with recorded SHA-256, and the upstream handoff document.

### Changed — Gateway declares its parked-run state non-durable (epic 054 / U6)
`AgentGateway` now explicitly declares that parked/awaiting-approval runs live in process-local
memory and are lost on restart (`durable_parked_runs = False`), and offers
`require_durable_recovery()` to fail a deployment-readiness check fast when durable recovery is
required — instead of implying restart-safety it does not have. The module docstring documents the
supported alternative: the direct embedding path (durable `SqliteRuntimeStore`/`PostgresRuntimeStore`
checkpoint store + `SqliteCommandQueueStore` + `RunAbortHandle`), which already exposes every durable
primitive a host needs without the Gateway. No behaviour change to `submit`/`respond`/`pending`.

### Compatibility — this Unreleased set is additive and persisted-state compatible
All Unreleased changes (U2 gate provenance, U3 `ResumeCommand` fields, U4 cancellation hook, U5 plan
binding, U6/U7) are additive: new optional contract fields default to `None`/absent, new metadata
keys (`_ad_gate_provenance`, `consumed_approvals`, `approved_plan.policy_binding`) are additive, and
existing checkpoints/events remain readable. No public symbol was removed or renamed.

### Added — U4: abort observed during an in-flight LLM call (epic 052 / U4) — U4 complete
An abort is now observed *while a provider call is in flight* instead of only at the next step
boundary: `_await_with_redirect` (the existing redirect-race helper) also polls the run's abort handle
and, on a stop, cancels just that request and raises a typed `AbortRequested`. The signal is a plain
`Exception` distinct from the provider-error types, so it escapes every `except httpx.*`/`RuntimeError`/
`ValueError` clause on the completion path and is mapped **explicitly** to a `CANCELLED_BY_USER`
terminal by the runner loop (a dedicated `except AbortRequested` beside the wall-clock `except
TimeoutError`) — no mis-mapping to `MODEL_ERROR`/`RUNTIME_ERROR`. Inert unless an abort handle is
supplied; the redirect path is unchanged. A stuck 10s provider call aborts within the ~0.1s poll
interval as a truthful cancellation. Full sweep 2961 passed (+1). This closes the last U4 item — U4
(durable stop + host cancellation) is now complete across the hook, durable lifecycle, transition
checks, `CANCELLATION_FAILED`, late-result fencing, and mid-call abort.

### Added — F3: monotonic checkpoint revision + expected-revision resume guard (epic 051 / U3)
`CheckpointRef` gains a monotonic per-run `revision` (0 for the first checkpoint, +1 per save along
the parent chain, derived in `build_checkpoint_ref` from the already-threaded previous row — no extra
store read). This replaces the meaningless static `state_version="v1"` as the run's ordering/position
signal (`state_version` stays the serialization format). `ResumeCommand.expected_revision` uses it for
revision-based optimistic concurrency: when set, a resume applies only if the pending interrupt's
checkpoint is at that revision, otherwise a stable `ResumeConflictError` — more robust than
`expected_checkpoint_id` because the revision is ordered. Variant A (additive): `latest()` ordering is
unchanged across the four stores, so no behaviour change and conformance tests are untouched (fields
default `revision=0` / `expected_revision=None`). Full sweep 2960 passed (+3). F3 foundation from the
backlog design-notes; the store-`latest()`-by-revision rewire (Variant B) remains deferred.

### Added — F2: prior-result replay for duplicate approvals (epic 051 / U3)
The atomic approval-consumption ledger now durably records the winning consume's **terminal output**
(full `AgentRunOutput` JSON), and a new `RunnerConfig(replay_prior_result=True)` makes a duplicate
approve of an already-consumed interrupt **replay that output verbatim** instead of raising a conflict
— completing the U3 DoD "a concurrent duplicate returns the prior recorded result and never
re-executes the tool". `ApprovalConsumptionStore` gained a `result_payload` column/field (SQLite
migrates in place), `record_result(..., result_payload=...)`, and `ConsumeOutcome.prior_result_payload`;
the runner records the output at each consumed terminal and short-circuits a duplicate to the stored
output before re-driving. Off by default (`replay_prior_result=False` → duplicate still raises the
stable `ResumeConflictError`), so fully backward-compatible. Full sweep 2957 passed (+2). This is the
F2 foundation from the backlog design-notes.

### Added — U4 result fencing enforce: drop superseded stragglers (epic 052 / U4, on F1)
Building on the F1 attempt-epoch foundation, the tool-stage fold now **stamps** each result envelope
with the run's current `attempt_epoch` (attribution: a persisted/traced result records which attempt
produced it) and **fences** any straggler carrying an *older* epoch — a result from a superseded
attempt (e.g. across a second resume) is dropped instead of folded back in, and a new
`RuntimeEventType.RESULT_FENCED` event is emitted. Fresh runs (epoch 0) neither stamp nor fence, so
behaviour is unchanged; the drop only triggers for a result stamped under an earlier attempt than the
run's current one. Full sweep 2955 passed (+3). This closes the U4 late-result fencing gap; the
remaining U4 item is observing an abort *during* an in-flight LLM await.

### Added — F1: attempt-epoch result-fencing foundation (epic 052 / U4)
Foundation for late-result fencing: a monotonic per-run `RunContext.attempt_epoch` (0 for a fresh
run, bumped on each resume that re-drives the run, persisted via checkpoint metadata) is now exposed
to tool handlers for the duration of the tool stage via `current_tool_attempt_epoch()` (same
context-var idiom as the cancellation hook). New `agent_driver.runtime.single_agent.fencing` provides
the pure guard primitives — `stamp_attempt_epoch` (stamps the reserved `_ad_attempt_epoch` key only
when epoch > 0, so fresh runs are byte-identical), `attempt_epoch_of`, and `is_stale_attempt` — plus
the `TerminalReason.LATE_RESULT_IGNORED` vocabulary. This is the attribution layer the U4 fencing step
builds on to drop a straggler result from a superseded attempt; it is observe-only and
behaviour-neutral on its own (fresh runs stay epoch 0). Full sweep 2952 passed (+4). Remaining U4:
the enforce step (stamp result envelopes + drop stale + `RESULT_FENCED` event) and mid-in-flight-LLM
abort.

### Added — durable PlanArtifact on plan approval + CANCELLATION_FAILED terminal (U5/U4)
- U5 (epic 053): a plan-approval resume now writes a durable, hash-bound `PlanArtifact` (APPROVED on
  approve/edit, REJECTED on reject) when an optional `RunnerConfig(plan_artifact_store=...)` is
  configured — the previously-unwired `PlanArtifactStore` is now connected via the same optional-dep
  pattern as the approval/abort stores. Off (default) → no change.
- U4 (epic 052): new `TerminalReason.CANCELLATION_FAILED`. When a stop is requested but a handler
  ignores cooperative cancellation and the step blows the wall-clock guard, the terminal is now the
  truthful `CANCELLATION_FAILED` (an enforced stop) instead of a plain `DEADLINE_EXCEEDED`; the abort
  lifecycle ledger records it as observed→cancelled. A plain timeout with no abort in play stays
  `DEADLINE_EXCEEDED`.

Both additive/behaviour-neutral; full sweep 2948 passed. Remaining U4: a fencing/epoch token against
late handler results (+ `LATE_RESULT_IGNORED`) and mid-in-flight-LLM-await abort.

### Added — plan-integrity enforcement: gate on the plan hash, not just presence (epic 053 / U5)
The force-planning gate (`_force_planning_has_approved_plan`) previously counted any approval with a
`plan_id` as sufficient — it never compared the plan's content. A new
`PlanningPolicyInput.required_plan_hash` closes that: when set, the recorded approval only counts if
its `approved_plan.content_hash` equals the required hash, so a materially revised plan (different
hash) is treated as unapproved and re-gated (DENY) before any write runs. A host that requires
re-approval on a plan change sets `required_plan_hash` to the hash it reviewed (pairs with U5's
harness-authoritative hashing and the `plan_policy_binding`). When unset (default) the gate keeps its
presence-only behaviour — fully backward compatible. Additive; full sweep 2939 passed (+6). Remaining
U5: connect the durable `PlanArtifactStore` (still unwired) and carry the binding through the trace
projection.

### Added — plan-integrity: authoritative hash, EDIT re-hash, host policy-binding (epic 053 / U5)
The approved plan's content hash is now **harness-authored** rather than trusted from the model/tool
`content_hash` field: `_mark_force_planning_approved` recomputes `plan_content_hash(...)` from the
content actually approved, and on an EDIT resume it re-hashes from the operator's **edited** content
(fixing a stale-hash bug where an edited plan kept the original hash). A host can therefore detect a
material plan revision before authorising execution via the new public
`agent_driver.context.planning.detect_plan_revision(approved_hash, candidate_content)`. An optional
opaque host policy-binding (`ResumeCommand.metadata["plan_policy_binding"]`) and `approved_by` now
survive into the approved-plan record (`force_planning.approved_plan`) and the checkpoint — sourced
from the resume command, so model/tool output cannot forge them. Additive and behaviour-neutral;
full sweep 2901 passed (+4). Remaining U5 (epic 053): wire the enforcing gate
(`_force_planning_has_approved_plan`) to compare the hash (today presence-only), connect the durable
`PlanArtifactStore` (still unwired), and carry the binding through the trace projection.

### Added — durable abort lifecycle ledger (epic 052 / U4, phases A/D)
`RunAbortHandle` is process-local — its flag vanishes on restart, and the durable
`DurableAbortRequestRecord.observed` field was never actually set — so after a crash a host could not
tell whether a stop was *observed and cancelled the run* or the *run completed before the stop
landed*. New `agent_driver.runtime.control.AbortLifecycleStore` (in-memory + SQLite, re-exported from
the `agent_driver.runtime` facade) makes that lifecycle real and restart-queryable: `requested →
observed → cancelled | completed_before_cancel`. `request_abort` records an actor/reason-correlated
durable stop request (issuable from another process); `mark_observed` sets `observed=True` (the
transition the old record never made); `resolve` records the truthful terminal outcome. Wired as an
optional `RunnerConfig(abort_store=...)` dep: the runner finalises the record after each terminal run
— a user-cancelled run becomes observed→cancelled, a run that finished while a stop was pending
becomes completed_before_cancel, and a clean run leaves no record. Additive/behaviour-neutral (no
store → unchanged); full sweep 2933 passed (+14). On B (abort checked before every transition): the
step-boundary check already runs before each plan/LLM/tool step, and the tool stage skips a
not-yet-started call once an abort is observed (the U4 hook slice's `run_aborted` block) — the
remaining item is observing an abort *during* an in-flight LLM await (a responsiveness improvement
that needs care to keep the terminal reason truthful, deferred). Remaining U4 D: a fencing/epoch
token so a late handler result cannot reopen a run, and distinct terminal reasons for
cancellation-failed / late-result-ignored.

### Added — cooperative host cancellation hook into running tool handlers (epic 052 / U4, hook slice)
A running tool handler could not observe the run's process-local `RunAbortHandle`, so a host had
no way to cancel its own in-flight work (a socket, a browser, a long query) when a run was stopped.
The governed executor now optionally accepts the run's `abort_handle` (threaded from the step loop
exactly like `tool_gate`, only when set → old executor signatures unaffected) and, per call, exposes
a `ToolCancellation` (run/call/attempt identity + `is_cancelled` + `await wait_cancelled()`) to the
handler via the same context-var idiom as `report_tool_progress` — `current_tool_cancellation()`.
Handlers that never consult it keep the plain `Callable[[dict], Awaitable[dict]]` signature and incur
no overhead. Additionally, once an abort is already observed, a not-yet-started call is skipped with a
`run_aborted` block instead of launching new (possibly external) side-effecting work. New module
`agent_driver.tools.cancellation` (`ToolCancellation`, `ToolCancelledError`); scope/accessor in
`agent_driver.tools.context`. Additive and behaviour-neutral (no abort handle → token is `None`);
full sweep 2897 passed (+4). Remaining U4 (epic 052 A/B/D): a durable abort lifecycle
(`requested → observed → cancelled | completed_before_cancel`) that actually sets `observed` and
survives restart, abort observation inside the mid-LLM await (today only the redirect probe is
polled there), a bounded cancellation deadline on the token, terminal-reason honesty
(cancelled vs completed-before vs cancellation-failed vs late-result-ignored), a fencing token
against late handler results, and the uncooperative-handler / restart adversarial matrix.

### Added — atomic, durable approval-consumption ledger (epic 051 / U3, phases B/C/D)
The resume path's duplicate-approval guard was a TOCTOU read of the latest checkpoint's
`consumed_approvals`: it only recognised a duplicate *after* the first approval's post-consume
checkpoint committed, so two clients approving the same interrupt in the pre-commit window could both
drive the run and execute the tool twice. New `agent_driver.runtime.control.ApprovalConsumptionStore`
(in-memory + SQLite impls, re-exported from the `agent_driver.runtime` facade) closes that with a
**compare-and-swap** ledger: the first `try_consume` for an interrupt wins via an atomic
`INSERT OR IGNORE` (SQLite) / lock-guarded insert (in-memory), any concurrent or later duplicate loses
and is refused with `ResumeConflictError` *before* the tool runs — the exactly-once gate, which also
survives a crash between consume and result because the row is written before execution. Wired as an
optional `RunnerConfig(approval_store=...)` dep consulted in `_handle_resume_with_pending`; when unset
(default) the resume path keeps its prior TOCTOU + expected-checkpoint behaviour (fully
backward-compatible). Tests prove one tool side-effect under 16-thread SQLite contention, two
concurrent async resumes, restart (new store instance sees the consumption), conflicting decisions,
and idempotency-key duplicates. Full sweep 2919 passed (+12). Remaining U3: prior-result full replay
(the ledger records a result ref; returning the prior `AgentRunOutput` verbatim is not yet wired) and
a monotonic checkpoint revision to replace the static `state_version="v1"`.

### Added — expected-checkpoint + idempotent approval consumption (epic 051 / U3, contract slice)
`ResumeCommand` gains two optional, backward-compatible fields for durable approval:
`idempotency_key` (a host-supplied key identifying one logical approval attempt) and
`expected_checkpoint_id` (an optimistic-concurrency guard). The resume path now raises a new
`ResumeConflictError` (subclass of `RuntimeExecutionError`, so existing base-type catchers keep
working) in two cases: **(1)** the approval names an `expected_checkpoint_id` that no longer matches
the pending interrupt's checkpoint (a *stale* approval against a run that moved on), and **(2)** the
targeted interrupt was already consumed by a prior resume — matched by interrupt id *or* by
`idempotency_key`, so an HTTP-style duplicate is recognised even if it does not re-send the same
interrupt id. Consumed interrupts are recorded in a durable `consumed_approvals` checkpoint-metadata
list, so a duplicate approval is reported as an idempotent no-op instead of re-executing the tool.
Additive and behaviour-neutral when the new fields are unset; full sweep 2893 passed (+4, proving one
tool side-effect under duplicate/stale approvals). Remaining U3 (epic 051 phases B/C/D): true
concurrent compare-and-swap and a Postgres durable approval store, binding the interrupt to a real
checkpoint id (it still carries the `checkpoint_pending` sentinel), prior-result replay, a
crash-after-consume effect ledger, and the two-client/restart adversarial matrix against a real
durable store.

### Changed — unify the interrupt-id / attempt-id derivation across both builders (epic 050 / U2)
The two interrupt builders minted ids by different schemes — the tool-approval / gate path
(`policy_interrupt`) used `int_{run_id}_{index}` while the allow-path clarification / wait-for-event /
plan-approval interrupts (`allowed.py`) used `int_{tool_call_id or index}`. Both now route through one
shared helper (`agent_driver.tools.executor.interrupt_ids.build_interrupt_id` /
`build_attempt_id`): a **run-scoped, per-call-stable** id `int_{run_id}_{tool_call_id or index}`
(prefers the harness tool_call_id so the id survives gate → interrupt → approval/resume; equal to the
old tool-approval scheme when no tool_call_id exists). Safe because resume correlation only ever
matches the *echoed* `pending.interrupt.interrupt_id` (nothing reconstructs the id independently), so
the whole HITL suite passed unchanged. Full sweep 2964 passed (+3). This is one of the remaining U2
items; provenance projection through the terminal is the last U2 refinement.

### Added — tool-gate call identity + decision provenance (epic 050 / U2, phases A/B/D)
The dynamic `ToolGate` seam now carries **stable call identity** and an optional **host
provenance** channel, for embedders (e.g. PentestLens) that bind an external policy decision to a
specific planned call. `ToolGateContext` gains `tool_call_id` and `attempt_id`; `ToolGateAllow`,
`ToolGateDeny`, and `ToolGateAsk` gain an optional `provenance: GateProvenance` (`decision_id`,
`policy_snapshot_id`, bounded JSON-safe `metadata`). Validated provenance is folded onto the tool
policy outcome under a reserved `_ad_gate_provenance` key (namespace `agent_driver.contracts.
validation.RESERVED_METADATA_PREFIX = "_ad_"`), and — on the ask path — forwarded into the approval
interrupt (and its envelope) the host resumes against, merged last so neither host run-metadata nor
model/tool output can overwrite it. New `ensure_bounded_json_metadata` fails **closed** (translates
the gate result to DENY) on non-JSON, oversized, too-deep, too-many-key, or reserved-namespace host
metadata, so a malformed or forged payload cannot pass unaudited. `GateProvenance` is exported from
the `agent_driver.runtime` facade. Additive and behaviour-neutral for gates that don't use the new
fields; full sweep 2889 passed (+16). Remaining U2 work (epic 050 phases C/E): propagate provenance
through event log / trace / terminal projections, unify the two `interrupt_id`/`attempt_id`
derivations (`policy_interrupt` index-based vs `allowed` call-id-based), and the full
retry/timeout/abort adversarial matrix.

### Changed — refactor: decompose the provider_catalog god-module (behaviour-neutral)
`llm/provider_catalog.py` was 1193 lines and was earlier skipped as "mutually recursive". An
AST call-graph scan (Tarjan SCC + topological layering) disproved that: the graph is a DAG.
The earlier failed attempts partitioned by *name* (`seed_*` vs `build_*`) which cut across
layers; the correct seam is a **topological layer cut**. The lower layer (leaf helpers,
per-item fixture/plan builders, the `ProviderPluginRegistry` class, and the `_REPORT_VERSION`
constant — depth ≤ 2 in the call graph) was extracted into `llm/provider_catalog_fixtures.py`;
the report/orchestration layer (`build_provider_compatibility_report`,
`seed_provider_preflight_reports`, `seed_provider_routing_plans`,
`write_provider_catalog_artifacts`) stays in `provider_catalog` and imports the base
one-directionally — no cycle. All names remain re-exported from `provider_catalog` so
`context_windows` (which imports `seed_provider_catalogs`) and the tests are unaffected. Pure
structural change; full sweep 2873 passed. `provider_catalog.py` is now 264 lines (−78%).

### Changed — test: `--import-mode=importlib` is now the pytest default
Two test files share a basename (`tests/harness/test_lifecycle_hooks.py` and
`tests/runtime/test_lifecycle_hooks.py`) with no `__init__.py`, so plain `pytest` / `make
test` aborted collection under the legacy prepend mode with *import file mismatch* — every
contributor had to pass the flag by hand. Set it in `pyproject.toml` `addopts`; plain
`pytest` now collects the whole suite cleanly.

### Changed — refactor: decompose the subagents/executor god-module (behaviour-neutral)
`subagents/executor.py` was 1090 lines. An AST call-graph scan (including async defs) showed
the 24 synchronous child-run helpers form a clean leaf layer — they build a child
`AgentRunInput` and turn a child `AgentRunOutput` into a `SubagentRun`, without calling the
async execution spine or referencing the `SubagentExecutionResult` class. They were extracted
into `subagents/child_helpers.py` together with the 7 child-run tuning constants and the
`ChildRunner` type alias, and re-imported into `executor` (a test imports `_child_budget_summary`
directly, so re-export matters). No sibling module imports `executor`, so the split is a clean
DAG. Pure structural change; full sweep 2873 passed. `executor.py` is now 689 lines (−37%).

### Changed — refactor: decompose the durable_lifecycle god-module (behaviour-neutral)
`harness/durable_lifecycle.py` was 1204 lines. An AST call-graph scan showed the seed
fixtures are tightly coupled to the `DurableLifecycleRepository` class (they construct it),
so the cleaner seam was the report layer: the compatibility-report build + markdown render +
artifact write functions (plus their private helpers and the `_DURABLE_SCENARIOS` scenario
table) operate on a repository passed in, referencing the class only in a parameter
annotation. They were extracted into `harness/durable_lifecycle_report.py` with a
`TYPE_CHECKING`-only class import (the annotation is a string under `from __future__ import
annotations`, never evaluated), so the split stays a DAG with no runtime cycle. Pure
structural change; full sweep 2873 passed. `durable_lifecycle.py` is now 899 lines (−25%).

### Changed — refactor: decompose the capability_packs god-module (behaviour-neutral)
`harness/capability_packs.py` was 1218 lines, dominated by ~800 lines of pure seed fixtures
(one `seed_scenario_specs` alone is ~585). An AST call-graph scan confirmed the fixtures are
a leaf layer (resolution depends on fixtures, never the reverse), so they were extracted into
`harness/capability_packs_fixtures.py` and re-imported. The one shared constant
(`_LIVE_SKIP_REASON`) moved to the base fixtures module and is imported back, keeping the split
a DAG. Pure structural change; full sweep 2873 passed. `capability_packs.py` is now 376 lines
(−69% — the module is now the resolution layer it was named for).

### Changed — refactor: decompose the skills/lifecycle god-module (behaviour-neutral)
`skills/lifecycle.py` was 1484 lines (46 top-level defs). The evidence/reporting layer was
extracted; the shared product-family helpers were lifted into a small base module so the
split is a DAG (no back-import). Pure structural change; full sweep 2873 passed. `lifecycle.py`
is now 1191 lines (−20%).
- `skills/lifecycle_common.py` — the two shared product-family mappings
  (`_primary_skill_scenario`, `_pack_id_for_product`), imported by both `lifecycle` and the
  new evidence module (base layer, no back-dependency).
- `skills/lifecycle_evidence.py` — evidence index + markdown render + artifact write + replay
  (`build_skill_lifecycle_evidence_index`, `render_skill_lifecycle_markdown`,
  `write_skill_lifecycle_artifacts`, `replay_skill_lifecycle_from_artifacts`) plus their private
  helpers. Re-exported from `lifecycle` for existing callers/tests.

### Changed — refactor: decompose the tool_stage god-module (behaviour-neutral)
`runtime/single_agent/tool_stage/__init__.py` was a 1477-line package init doing real work
(24 top-level defs). Two cohesive, leaf function groups were extracted into sibling modules
and re-imported into `__init__`, so every existing import — including the private `_foo`
helpers that tests import directly — keeps working unchanged. Pure structural change; the
full sweep stays at 2873 passed. `__init__` is now 948 lines (−36%).
- `tool_stage/recovery.py` — the synthetic recovery/repair-hint appenders
  (`_append_tool_call_parse_error_feedback`, `_append_denial_recovery_message`,
  `_append_unknown_tool_recovery_message`, and the disallowed-management / python-policy hints).
- `tool_stage/protocol_messages.py` — the TOOL-message protocol compaction/normalization
  helpers (`_compact_tool_payload_for_protocol`, `_compact_generic_tool_payload_for_protocol`,
  `_normalize_protocol_messages`, `_is_drop_candidate_assistant_message`, `_load_protocol_messages`).

### Added — SDK developer-experience quick-wins
Cheap, additive DX improvements from a broad SDK/docs audit — no public method removed
(back-compat preserved). **9 new tests; full sweep 2873 passed.**
- **`Agent.add_tool(fn)`** — register a custom tool on a live agent; it is callable on the
  next turn with no separate `ToolSet.only(...)` to keep in sync (registering-but-forgetting-
  to-select was the classic foot-gun). Accepts an async function (arg schema inferred from the
  signature) or a `tool(...)` definition, and works as a decorator `@agent.add_tool(name=...)`.
  Registers into the agent's live `deps.tool_registry`, which the request builder reads.
- **`agent_driver.sdk` now re-exports the custom-tool primitives** `tool`, `ToolRegistry`,
  `register_custom_function`, `CustomToolDefinition` — building a registry no longer forces an
  import from `agent_driver.tools` alongside the SDK facade.
- **`Agent.stream_run(..., stream_poll_interval_ms=...)`** — a typed parameter replacing the
  undocumented `app_metadata["stream_poll_interval_ms"]` magic key (still honored for
  back-compat; the typed arg wins when both are set).
- **Docs hygiene**: moved six dated 2026-05-31 plan/analysis docs to `docs/archive/` (links
  updated); normalized cookbook `FakeProvider` imports to the blessed `agent_driver.llm` path;
  `docs/sdk-tools.md` now leads with the `add_tool` one-liner.

### Fixed — structure-preserving truncation of JSON tool results
A TOOL protocol message's content is `json.dumps` of a tool's `structured_output`. Two
context passes shortened it with a raw `content[:N]` slice, cutting the JSON
mid-structure — so any consumer that reads tool-result content as strict JSON (an
external / host parser, or a provider validating the replayed transcript) received
malformed data. (The executor layer was already safe — it spills the full payload and
only shortens a preview; the break was downstream in compaction/trimming.) Reference:
hermes `_truncate_tool_call_args_json`, whose byte-slice predecessor drew non-retryable
provider 400s and looped the session re-sending broken history. **7 new tests; full
sweep 2864 passed.**
- **`agent_driver.context.tool_content_shrink.shrink_json_tool_content`** — parses a
  serialized-JSON tool result and truncates only its long **string leaves** in-tree
  (inline `…[+N chars]` marker), re-serializing to still-valid JSON; the object/array
  shape and all keys survive by construction. Returns the input unchanged when nothing
  exceeds the leaf budget (idempotent), or `None` when the content is not a JSON
  object/array (caller falls back to its own plain-text slice — safe for prose).
- Wired into both raw-slice sites: `context/compaction/tool_history.py`
  (`compress_tool_history` mid-tier, epic 035 A) and `context/trimming/deterministic.py`
  (last-message budget truncation). A JSON tool message is never char-sliced; non-JSON
  prose keeps the existing marker slice.

### Added — SQLite durability hardening (horizon-scan 046 #1)
A storage-contention timeout must never abort an otherwise-healthy turn, and degraded
concurrency must never be silent (reference: hermes production incident — 10.8GB db, 9
concurrent processes). Our SQLite stores were unhardened: three `sqlite3.connect` sites
that weren't centralized, `journal_mode=WAL` set in only one, and no write-lock patience
anywhere. **6 new tests; full sweep 2857 passed.**
- **`agent_driver.persistence.open_sqlite_connection`** — the single canonical opener
  (stdlib-only). Sets `busy_timeout` (default 30s) so a writer waits out a sibling holding
  the DB (VACUUM after auto-prune, WAL truncate-checkpoint on close, mixed-version process
  during a rolling deploy) instead of failing fast; applies `journal_mode=WAL` and **verifies
  the value SQLite actually returns** — on NFS/SMB/overlay filesystems `PRAGMA journal_mode=WAL`
  silently returns `delete` (reader-blocks-writer), which now logs a warning, or raises the
  typed `WalUnsupportedError` when `require_wal=True`.
- **All three connect sites routed through it**: `SqliteStoreBase` (sessions / artifacts /
  context / plan-artifact / record stores) and the two subagent stores (`subagents/store.py`,
  `subagents/mailbox.py`) — the latter two previously opened with no WAL and no busy_timeout.

### Changed — phase-updates to existing epics (horizon-scan 040)
Four small, self-contained hardenings surfaced by the reference scan. **13 new tests;
full sweep 2851 passed.**
- **016+ (error classifier)**: a malformed-body 400 (`text content blocks must be
  non-empty`, `invalid_request_body`, …) now classifies as `FORMAT_ERROR`, not
  `CONTEXT_OVERFLOW` — so it never triggers destructive compression (overflow is an
  "attractive nuisance"; its recovery must not run on a body error). `throttling`/`rate
  limit` on a 400/422 classifies as `RATE_LIMIT` (backoff), ahead of overflow's "too many
  tokens". A litellm/Bedrock error envelope (`{errorMessage, errorCode, errorArgs.reason}`)
  is unwrapped so the real reason is matched, not the JSON wrapper. References: hermes
  `207a6c969` / `53bfe40a3`.
- **019+ (tool-failure streak guard)**: a parallel fan-out of N calls failing with the same
  signature in one turn now advances the streak by **1, not N** — the threshold measures
  "turns that failed to adapt", and the model hasn't seen this turn's results yet. Reference:
  openclaude `9d5b77d`.
- **033+ (blind-call schema probe)**: a deferred tool (omitted from the prompt schema,
  rediscovered via `tool_search`) invoked without a schema-`required` argument returns its
  schema instead of dispatching blind — cheap models looped ~30 invalid calls until the
  budget died. Key-absence only, fails open. Reference: hermes `8fbe2e388`.
- **037+ (observer fault containment)**: locked with an invariant test — a lifecycle observer
  that raises in `after_llm_response` / `on_run_completed` never alters the run outcome nor
  propagates out of the dispatch loop (behaviour already isolated; reference: openclaude
  `c23b6e1`).
- Deliberately N/A to our architecture (documented in scan 040): 028 prune-hysteresis (no
  proactive per-iteration prune loop), 035 `[SKILL_PRUNED]` marker (skills are tool-results,
  covered by 035 tool-history compression). 018 conformance-vectors deferred (test infra).

### Added — event-driven wait: park-on-event instead of polling (epic 045)
A run told to "wait until the background build finishes" had to poll a status tool in a
loop — burning steps (the 019 per-turn caps punish it) and re-reading the whole context
every poll. New `wait_for_event` primitive parks the run on an external event via the
existing interrupt/resume machinery: the loop is checkpointed and released, then resumes
when the host delivers the event. Domain-neutral — the engine provides the subscription +
pause/resume + bounded-deadline liveness; the actual event sources (process exit, webhook,
file, queue) and delivery live in the host. **17 new tests; full sweep 2838 passed.**
- **Contracts** (`agent_driver.contracts.wait_for_event`, re-exported from
  `agent_driver.contracts`): `WaitForEventRequest` (`event_key`, `deadline_seconds`,
  `poll_fallback_seconds`, `description`), `WaitForEventResolution`, `WaitForEventStatus`,
  `wait_for_event_resolution_from_resume`, `clamp_wait_deadline`. New
  `InterruptReason.WAIT_FOR_EVENT`.
- **Liveness (tie-in to 041)**: a subscription is ALWAYS bounded — `deadline_seconds` is
  clamped into `[1, 86400]` with a 3600 default, so a parked wait can never hang forever. A
  wait that never fires degrades to a `timed_out` resolution (host sets the timeout marker
  in the resume state-patch).
- **Primitive**: a `wait_for_event` builtin tool (its description tells the model to prefer
  it over a poll loop for any wait longer than a few seconds) whose call the executor
  converts to a `WAIT_FOR_EVENT` interrupt (`allowed_actions` CLARIFY/CANCEL). The run pauses
  with the subscription in the paused output; the host subscribes to the real source and
  resumes via `CLARIFY` (deliver payload) or `CANCEL`. Reuses the whole pause/resume/
  checkpoint plane — a parked run doesn't mutate the prompt prefix (friendly to 028).
- **Not in scope (documented)**: real event sources + crash-safe delivery-claim across
  restart (blueprint hermes `async_delegation`, deferred #14) — the next slice; the bounded
  deadline gives correctness without it today.

### Added — context-engine seam (epic 044)
Give RAG-heavy hosts a first-class way to see and target what fills the context window,
plus safety for per-turn context selection. The `select_context` mechanism already exists
(`RunLifecycleHook.before_llm_request` returns a replacement request without mutating
persisted history) and `on_turn_complete` is covered by `after_llm_response`/
`on_run_completed`; 044 adds the two genuinely-missing pieces. **11 new tests; full sweep
2821 passed.**
- **(A) `agent_driver.context.breakdown.estimate_context_breakdown`** — per-category
  `chars // 4` composition of the next request (`system_prompt / tool_definitions /
  tool_results / scaffolding / conversation`). The authoritative total (`total_chars // 4`)
  equals what the compaction trigger sees, so a host's `/context` number and the trigger
  never disagree (hermes `context_breakdown` invariant). Accepts `ChatMessage` objects or
  serialized dicts. Reference: hermes `context_breakdown.py`.
- **(B) fail-open hardening of `dispatch_before_llm`** — a select-context hook that filters
  everything out (the hermes `all([]) is True` trap) returned a request with no usable turn,
  blanking the prompt. A degenerate replacement (not request-shaped, or no non-system message
  survived) is now logged and dropped; the chain falls open to the prior request. The
  existing raise-isolation is unchanged.
- **(C) `context_breakdown` in the run's terminal metadata** — the `/context` equivalent,
  computed fail-open from the assembled `protocol_messages` (or the run input). A host reads
  the same categories/number the trigger uses.
- **Not in scope (documented)**: a separate `ContextEngine` ABC (our seam is already
  `RunLifecycleHook` — a parallel protocol would duplicate it); the retrieval-plugin product
  fit lives in the host (MeetScript / excel-ai).

### Added — tool-call wire integrity (epic 042)
Three independent wire bugs, each silently losing or corrupting a tool call. The plane
rule: never synthesize tool-call completeness. **12 new tests; full sweep 2810 passed.**
- **(A) tool_call_id collision dedup** (`tools/executor/planned.dedupe_tool_call_ids`, applied
  at the single ingest point `extract_planned_tool_calls`). Some providers reuse the same
  `tool_call_id` across a batch; tool-result rows are keyed by id, so the second call's result
  silently vanished from every replay. The nth occurrence of an id is renamed to `<id>_d<n>`
  deterministically (prompt-cache-stable). Reference: hermes `474c84ed8`.
- **(B) empty tool_calls contract violation → bounded re-prompt** (`tool_stage`). A provider
  that signals `finish_reason=tool_calls` but ships an empty array (observed: opus-4.8 /
  sonnet-4.5 on GitHub Copilot) made the loop finalize its narration — an unattended job
  "succeeded" at tool_turns=0. Now re-prompted for the call, bounded to 3 consecutive
  (`empty_tool_calls_reprompt_count`, reset on a successful tool round), signal
  `empty_tool_calls_contract_violation`. Gated so it never overrides a substantive answer
  (`final_content_unusable`) and never fires when the runtime itself suppressed a shipped call
  (`suppressed_planned_tool_calls`, forced-final/budget winddown). Reference: hermes `63954d508`.
- **(C) no repair-execute on non-terminal truncation** (`extract_planned_tool_calls`). A tool
  call whose args were repaired from truncated JSON is only trustworthy when the provider
  actually finished the turn; a stream ending with no terminal reason (`finish_reason=UNKNOWN`)
  is a transport cut, so a repaired call is dropped rather than executing half a command.
  Reference: openclaude `2fe1e1b`.

### Added — liveness plane: idle-bounded side/aux LLM calls (epic 041)
Side/aux LLM calls (compaction, structured extraction, suggestions, graders) called
`provider.complete` directly with NO timeout — a wedged provider blocked the whole run
forever (the main loop has always been protected by `LlmStreamIdleTimeout` + the epic-025
`stage_wait_heartbeat`, side calls were not). Closes that gap with a **liveness** (idle),
not wall-clock, timeout — the reference lesson (hermes reverted a wall-clock watchdog
because a 30s total deadline killed slow-but-healthy summary models). Opt-in; default
preserves today's unbounded behaviour. **12 new tests; full sweep 2802 passed.**
- **`agent_driver.llm.liveness.bounded_side_completion`** + `AuxIdleTimeout`. With an idle
  timeout set, the side call streams and re-aggregates the text, resetting the idle timer
  on every chunk — a model that keeps producing tokens is never killed no matter how long
  it runs; only a genuinely silent stream trips `AuxIdleTimeout`. A generous total ceiling
  (`max(600s, 4× idle)`) bounds a degenerate trickle. Side calls are text-only, so
  re-aggregation is a plain concatenation. `idle_timeout_seconds=None` is a pure passthrough
  to `provider.complete` (zero overhead). A provider without a usable `stream` falls back to
  `complete` under the total ceiling.
- **Config**: `CapabilitySettings.aux_idle_timeout_seconds: float | None = None` (threads
  through `RunnerConfig(aux_idle_timeout_seconds=…)`; delegating property on `RunnerConfig`).
- **Wired** into `aux_completion` (`llm.aux`) and the LLM-full compaction path
  (`run_full_llm_compaction` / `compaction_stage`): a stalled compaction summary provider now
  returns a graceful `success=False` result (circuit breaker bounds retries) with failure
  kind `llm_compaction_aux_idle_timeout`, instead of hanging the run.
- **Deferred (documented in the epic)**: subagent stall detection via progress-token
  (hermes `99a381f31`) — a larger parent↔child progress-plumbing effort, adjacent to 034/045.

### Added — transcript-poisoning hygiene (epic 043)
Four hygiene invariants that close the transcript-poisoning class: an assistant turn
exposing its own chain-of-thought reads as a prefill/reasoning-injection to provider
classifiers and can permanently blank a session (reference incident: hermes `cf0c42fa0`,
four bricked sessions in a week). No behavioural change on clean runs; all knobs default
to today's behaviour. **28 new tests; full sweep 2788 passed** (3 unrelated pre-existing
failures: `test_chat` budget-loop + 2 `phase6_metadata`, all fail identically on baseline).
- **A — inline CoT never persists.** New `agent_driver.llm.reasoning_hygiene.strip_leading_think_block`
  strips a leading `<think>…</think>` block (reasoning models served without a reasoning
  parser emit CoT inline in `content`). Applied at the two sites where assistant text enters
  replayable history: the tool-protocol assistant checkpoint
  (`runtime/single_agent/tool_stage`) and the terminal answer
  (`finalization/output._sanitize_terminal_answer`). The separate streamed-reasoning channel
  and the redirect checkpoint were already CoT-free (audited). Records
  `inline_reasoning_stripped_chars` when it fires.
- **B — single pre-send owner for empty non-final turns.** New
  `agent_driver.llm.message_hygiene.repair_empty_non_final_messages` pads an empty non-final
  user/assistant turn (strict providers reject "messages must have non-empty content"; an
  interrupted turn leaves exactly that shape). Copy-on-write, idempotent, never touches the
  final turn or a designed-empty carrier (tool_calls / reasoning echo / tool rows). Wired as
  one chokepoint at the top of the `complete_request` retry loop (covers the initial send and
  every retry rebuild) **and** in the aux/side path (`llm.aux.aux_completion`), so graders and
  compaction summaries share the invariant. Lives in the `llm` layer to avoid a layering
  inversion; re-exported from `llm_step.provider_requests`.
- **C — scaffolding tag for synthetic messages.** New `agent_driver.contracts.scaffolding`
  (`SCAFFOLDING_METADATA_KEY`, `scaffolding_metadata`, `is_scaffolding`, `scaffolding_kind`):
  one tag honored by persistence, compaction and display simultaneously. Runtime-injected
  USER-role turns (parse-error feedback, denial/unknown-tool recovery, disallowed-management
  and python-import hints, deep-research gate nudges, forced-final retries, todo re-injections,
  redirect interrupt checkpoint) now carry it; the genuine redirect *correction* stays untagged.
  Partial compaction relabels a tagged row as `runtime` (never folds it into user intent);
  LLM-full compaction drops tagged rows from the excerpt. Reference: hermes `923704c7c`.
- **D — poisoned-prefix quarantine in the empty-final ladder (epic 016).** New
  `message_hygiene.quarantine_inline_reasoning`: when the empty-final ladder is exhausted and
  the history still carries an assistant turn with inline CoT, the ladder emits a
  `poisoned_prefix_suspect` signal, sanitizes the suspect turn(s) and retries **once** (bounded,
  mirrors the `strip_reasoning_echo` retry) before the honest give-up signal. Metadata:
  `poisoned_prefix_quarantine_attempted` / `poisoned_prefix_suspect_turns` /
  `poisoned_prefix_quarantine_recovered`.

### Added — MCP governance & evidence plane (epic 014)
- A deterministic governance/provenance layer on top of the existing MCP transport
  (`agent_driver/mcp_server`) and client-tool catalog (`agent_driver/tools/builtin/mcp.py`).
  It answers *which* MCP servers/tools/resources were allowed, *why* a call was
  approved/asked/blocked, and records redaction-safe provenance — never raw resource bodies
  or credentials. No live MCP server is required; default tool-loop behavior is unchanged.
- **Contracts** (`agent_driver.contracts.mcp_governance`, re-exported from `agent_driver.contracts`):
  `McpServerDescriptor`, `McpRegistrySnapshot`, `McpToolResourceRef`, `McpApprovalPolicy`,
  `McpApprovalDecision`, `McpCallProvenanceRow`, `McpGovernanceUsageSummary`,
  `McpGovernanceCompatibilityReport`. All reject secret-shaped values (except env-var *names*)
  and raw body/content keys; an `auth_mode` safe-key exemption records the mechanism without
  leaking a credential.
- **Logic** (`agent_driver.mcp_server.governance`): registry snapshots from the MCP catalog,
  allowed-roots boundary checks, deterministic approval evaluation with explicit status
  precedence (`out_of_roots` > `oversized` > `blocked` > `filtered` > policy default; no
  matching policy → `no_claim`, never a silent allow), call-provenance rows for allowed calls,
  support-bundle projection, two-product (Excel AI + chat-demo) seed compatibility reports,
  evidence index, Markdown render, artifact writer and replay.
- **CLI** `agent-driver mcp-governance audit` writes a capability-pack-compatible artifact set
  (`mcp_registry_snapshot.json`, `mcp_approval_decisions.json`, `mcp_call_provenance.json`,
  `mcp_governance_report.{json,md}`, `validation_gates.json`, `evidence_index.json`,
  `manifest.json`) accepted by `agent-driver capability-pack audit --no-live --strict`; new
  `mcp_*` artifact types registered in `contracts.capability_packs`. Live MCP / Phoenix /
  Playwright / benchmark gates emit as `no_claim`. Tests: `tests/mcp_server/test_mcp_governance.py`.

### Added — single-provider backoff-retry (transient failover for one-provider setups)
- `HealthAwareRouter` falls over to a sibling provider on a transient failure (timeout / 5xx /
  transport), but with a **single** configured provider there is nothing to rotate to, so the
  same blip hard-failed the request — even though retrying the same provider after a short
  backoff would have recovered it (e.g. an OpenRouter latency spike that resolves in seconds).
- The router now backs off and retries the **same** provider a bounded number of times when it
  is the only one configured and the failure is provider-down (`classified.marks_unhealthy`:
  timeout / overloaded / 5xx / transport / unknown). Deterministic per-request failures (auth,
  content policy, context overflow) still fail fast and never retry. **Multi-provider routing is
  unchanged** — exhaustion across two providers still fails fast (no extra same-provider loop).
- **`HealthAwareRouter(single_provider_retry_max=2, single_provider_retry_base_seconds=1.0,
  single_provider_retry_cap_seconds=8.0)`** — exponential backoff capped; `single_provider_retry_max=0`
  restores the previous hard-fail-on-first-error behaviour. Tests:
  `tests/llm/test_router_single_provider_retry.py`.

### Added — defer primer: retrieval-primed surfacing of deferred tools (model-agnostic)
- Deferred tools (`manifest.should_defer`) are omitted from the schema list to keep the
  per-call prompt small, and normally re-surface only when the model calls `tool_search`.
  But weaker models often **don't** call it (deepseek-v4-flash never does), so a
  deferred-but-needed tool silently drops out of reach — which capped deferral at a tiny
  safe set and blocked the bigger prompt-cost win. The defer primer removes the dependency
  on model cooperation: before each LLM step the runtime scores the currently-deferred
  tools against the live conversation and surfaces the relevant ones **directly** into the
  schema list (via the existing explicit-allow path in `_request_tools_from_registry`).
  The long tail stays deferred; `tool_search` remains the backstop for whatever the primer
  misses. So deferral now works on any model, not just one that calls `tool_search`.
- **`RunnerConfig.defer_primer`** (default `None` → unchanged pure-`tool_search` behaviour):
  a `Callable[[DeferPrimerInput], Iterable[str]]` returning the deferred-tool names to
  surface this step. A surfaced name still passes through the allow/deny gate, so priming
  can never leak a denied tool. `keyword_relevance_primer()` is a generic, language-neutral
  default — two signals: an exact tool-name mention in the conversation (strong; models
  routinely name a remembered tool whose schema is absent) and meaningful token overlap of
  name+description (weak), top-`max_tools` over `min_overlap`. A domain consumer can pass a
  smarter primer (synonym map, embeddings) without touching the runtime — the relevance
  policy is the consumer's, the surfacing mechanism is the runtime's. New
  `runtime/single_agent/llm_step/defer_primer.py`; +11 tests.

### Added — self-consistency / sample-and-vote primitive (`sdk.run_self_consistent`)
- A generic runtime technique for beating per-task LLM non-determinism: run the SAME
  agent run N times and keep the plurality-vote answer. Works exactly when the model is
  right more often than any single wrong answer — the correct value is the plurality
  while wrong answers scatter, so voting recovers it. `run_self_consistent(agent,
  run_input, samples=N, key=...)` runs N samples concurrently (distinct run_ids, optional
  concurrency bound), maps each output to a caller-supplied hashable vote token (default:
  trimmed answer; abstain on empty/failed/raised), plurality-votes (deterministic
  lowest-index tie-break), and returns the backing output as `consensus` + the vote
  distribution + `confidence`. Model/domain-agnostic — the caller's `key` carries any
  domain normalization. +7 tests. Validated live on excel-ai's hardest task
  (ratio_ru_decimal, deepseek-v4-flash): wrong samples scatter (gt=31 → [31,31,31,31,8]),
  so single-run 0.75 lifts to voted 1.00 (4/4). The one lever that attacks the per-task
  variance floor directly (stronger models are incompatible/worse) — pure harness.

### Added — defensive default step backstop + soft-budget grace (loop termination)
Driven by a reference-runtime comparison (hermes-agent ships a hard 90-iteration
cap; openclaude leans on auto-compaction) and a live forced-budget experiment
(qwen3.6-plus, OpenRouter): a multi-step task with `max_tool_calls=2`, 3 repeats
per side. Grace OFF → 3/3 `FAILED` with a **0-length** answer; grace ON → 3/3
`COMPLETED` with a **~1.7k-char best-effort** answer synthesised from the partial
context already gathered. (A noisy full-suite A/B was the wrong instrument here:
grace only fires on the minority of runs that actually hit a budget, so run-to-run
sampling variance swamps the signal — the forced-budget experiment isolates it.)

- **`RunnerConfig.default_max_steps`** (default `80`) — a config-level backstop on
  the agent step loop. `AgentRunInput`'s `max_steps`/`max_tool_calls`/`deadline`/
  `cost_budget` all default to `None`, so a run whose model never reaches a final
  answer (e.g. a tool that always fails, a tool-calling spiral) looped forever and
  `journal._next_seq` is O(n) per emit → RAM into the GBs. The backstop applies only
  when the per-run `max_steps` is `None`; set `default_max_steps=None` to opt back
  into a fully unbounded loop. High enough to never truncate legitimate deep runs.
- **`RunnerConfig.budget_grace_enabled`** (default `True`) — when a *soft* budget
  (max_steps / max_tool_calls, including the backstop) is exhausted, grant one
  bounded forced-final synthesis window (tools disabled) so the model returns a
  best-effort answer from what it already gathered, instead of a bare `FAILED` with
  an empty answer — the "grace call" both reference runtimes ship. Bounded by 2 extra
  LLM steps so a model that ignores tools-disabled still terminates deterministically.
  A **cost** ceiling is deliberately excluded (a money cap should hard-stop, not spend
  one more call). New `budget_grace` axis on `agent-driver eval compare` to A/B it.
  +5 runtime tests. Changes terminal semantics: step/tool-call exhaustion now tends to
  `COMPLETED` (best-effort) rather than `FAILED`; disable per-run with the config flag.

### Added — opt-in `success_field` on `ToolManifest` (self-reported failures → FAILED)
- Tools that return a structured `{"success": False, "error": ...}` payload instead
  of raising were marked `COMPLETED` by the executor, forcing every consumer (FE
  timeline, eval harness, Phoenix) to re-classify status itself. `ToolManifest` now
  accepts `success_field: str | None` (default `None` — unchanged for all existing
  tools). When set and the structured output carries that field with a **falsy**
  value, the executor (`tools/executor/allowed.py`) marks the trace `FAILED`, lifts
  the payload's `error` into the trace `result_summary`/`error_code`, and attaches a
  `ToolError` to the envelope. Decision stays `ALLOW` (the tool executed; only the
  outcome failed). A missing field never forces a false `FAILED` (conservative). New
  `trace_spec_failed` helper. +9 tests. Removes excel-ai's per-consumer status
  re-classification band-aid and makes `DENIED`/`ERROR`/`FAILED` honest end-to-end.

### Fixed — broaden the `code_action` except clause (runtime errors → FAILED, not crash)
- `run_code_agent_stage` (`code_agent/profile.py`) caught only `CodeExecutionError`;
  any other exception from a tool called via `code_action` (KeyError/TypeError/
  network) propagated uncaught and crashed the whole run instead of producing a
  FAILED trace. A fallback `except Exception` now maps these to a redacted
  `code_runtime_error` FAILED trace (redacted to the exception type so a raw message
  can't leak untrusted internals). The interpreter is the trust boundary. +1 test.

### Fixed — deferred tools are now actually omitted from the LLM schema list
- `manifest.should_defer=True` was honored by the registry (`list_non_deferred`)
  but NOT by the single-agent request builder, which enumerated tool schemas via
  `list_registered` — so deferred tools still shipped in every prompt (the
  deference was a silent no-op). `_request_tools_from_registry` now skips
  `manifest.is_deferred()` tools from the schema enumeration (an explicit request
  allowlist naming one overrides). Deferred tools stay invocable (gated by
  `evaluate_tool_policy`, not the schema layer) and discoverable via
  `tool_search`. +3 tests. Surfaced wiring excel-ai's schema-cost reduction.

### Fixed — DeepSeek DSML tool-call parser tolerates ASCII pipes + whitespace
- The fallback text-form tool-call parser (`llm/tool_call_parser.py`) only matched
  DeepSeek's `DSML` tool-call leak when wrapped in the canonical FULLWIDTH `｜`
  (U+FF5C). The same leak appears with ASCII `|` pipes and whitespace around the
  markers (e.g. `< | DSML | tool_calls>`) depending on the provider/proxy + how the
  text is re-encoded; those variants parsed to **zero** tool calls, so the calls
  leaked into the answer AND never executed (model "describes but doesn't act").
  The DSML open/close/stray patterns now accept any mix of `｜`/`|` + optional
  whitespace. Still gated on the literal `"DSML"` marker, so prose can't false-match.
  Surfaced while debugging excel-ai edit runs on DeepSeek-v4-flash via OpenRouter.

### Added — enforce the hard-profile claim audit (opt-in)
- The hard Deep Research claim audit (`research/claims.jsonl`, auto-derived from
  the source ledger) is now enforceable at final-readiness, not just observed.
  Two new repair reasons gate finalization for a hard run: `hard_claims_unverified`
  (no verified claim row yet) and `hard_claims_unsupported` (the audit still lists
  unsupported claims). Each carries a targeted repair nudge + tool-choice override
  (open a source / re-read the audit). Enforcement is **opt-in** via
  `task_contract.hard_options.enforce_claims_audit` — default hard-profile
  behaviour is unchanged.

### Added — real PDF text extraction for hard Deep Research
- `pdf_read` now extracts page-aware text from fetched PDFs via the optional
  `[pdf]` extra (pypdf) instead of only echoing injected mock text. Outcomes are
  explicit: real extraction → `status="verified"` with per-page `page_citations`
  and `total_pages`; extractor not installed → `text_extraction_unavailable`;
  scanned/image-only PDF → `no_extractable_text`; malformed structure past the
  magic-byte check → `pdf_parse_failed`. Non-verified outcomes keep
  `verified_text=False` so they are never treated as verified evidence. Core
  stays dependency-light — absent the extra, behaviour degrades gracefully.

### Added — reliable tool-first workflow nodes (`NodeContract`)
- Opt-in `AgentRunInput.node_contract` runtime enforcement for harness/workflow
  nodes (see [docs/node-contract.md](docs/node-contract.md)):
  - **Layer A** (`require_callable_tools`): run-start policy↔registry validation;
    uncallable `allowed_tools` / `finalize_when_tools` surface a
    `node_contract_warning` event + `output.metadata["node_contract"]
    ["tool_policy_warnings"]` instead of being silently dropped.
  - **Layer B** (`require_tool_use`): proactive tool-use prelude (tools + target)
    woven into the system prompt, plus a reactive guard that reprompts a
    zero-tool-call finalize (`max_tool_use_reprompts`) and then stamps a typed
    `no_tool_use` violation rather than returning a silent generic answer.
  - **Layer C** (`finalize_when_tools` + the `on_tool_evidence` lifecycle hook):
    finalize directly from sufficient tool evidence with no extra LLM
    continuation; terminal answer + tool outputs preserved.
- `RunLifecycleHook.on_tool_evidence(context, envelopes) -> FinalizeNow | None`
  escape hatch (`stop_after_tool_evidence` / `finalize_when_tools_satisfy_contract`).
- `tool_call_completed` event rows now carry `output_preview` + `structured_output`
  for downstream normalization.

### Added — cross-harness capabilities (E1–E8 + T0)
- Auxiliary cheap-model routing for side tasks (`RunnerConfig.auxiliary_provider`
  / `auxiliary_model`); compaction spend separated by model in the cost ledger.
- Tool-call argument truncation pre-pass before compaction
  (`enable_tool_arg_truncation`).
- Project-memory files (AGENTS.md/CLAUDE.md) layered into the system prompt
  (`project_memory_sources`), scanned for prompt-injection at ingestion.
- Ingestion injection/C2 scanner (`agent_driver.security.scan_context_text`),
  wired into project memory, skills, and recalled long-term memory.
- Configurable tool concurrency (`tool_concurrency_limit`).
- Per-subagent-type model routing (`subagent_model_routing`).
- Message sanitization (lone-surrogate / NUL stripping) before provider calls.
- Anthropic prompt-cache breakpoints (tools → system → conversation)
  (`enable_prompt_cache`).
- Declarative harness profiles (per-model prompt slots / tool exclusion /
  description overrides).
- Pluggable filesystem backends (`agent_driver.fs`): `FileBackend` protocol,
  `StateBackend`, `LocalFilesystemBackend`, `CompositeBackend`.
- Low-budget evaluation harness (`agent_driver.evals`): N-run aggregation,
  baseline-vs-treatment comparison, open-weight presets, `general_task_suite`,
  and the `agent-driver eval compare` CLI.

### Added — subagents & governance
- In-process background subagents (`AsyncSubagentManager` / `BackgroundSubagent`):
  start / check / cancel by task id.
- Scope-aware human-in-the-loop predicate (`PermissionRule.path_under`): approve
  only when a bulk/glob op could touch a protected path.

### Added — SDK & library readiness
- Grouped capability config (`CapabilitySettings`) with backward-compatible flat
  `RunnerConfig` kwargs.
- Construction-time default `tool_gate` on `create_agent` (no per-call threading).
- PEP 561 `py.typed` marker; documented public embedding surface
  ([docs/embedding.md](docs/embedding.md)).

### Changed
- `BatchRunner` retries transient failures (rate-limit/429, overload, timeout,
  server, transport) with backoff and fails fast on non-transient ones
  (auth, billing/402, model-not-found, content-policy, context-overflow).
- OpenRouter open-weight list prices registered in the cost ledger.

## [0.1.0]

Initial baseline: durable single-agent runtime (step loop, checkpoints, event
log, replay, resume, interrupts), context management (trimming / compaction /
token pressure), governed tools (manifests, policy, guardrails, gate),
permissions, planning & steering, subagents, long-term memory, lifecycle hooks,
providers (fake / OpenAI-compatible / Ollama / Anthropic) with descriptors,
router and error classification, cost ledger, scheduler, gateway core, batch
trajectories, MCP server, and the SDK facade.
