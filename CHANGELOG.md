# Changelog

All notable changes to `agent-driver` are recorded here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); the project is pre-1.0 (`0.x`),
so the public surface (see [docs/embedding.md](docs/embedding.md)) may still
change between minor versions.

## [Unreleased]

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
