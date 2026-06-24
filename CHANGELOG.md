# Changelog

All notable changes to `agent-driver` are recorded here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); the project is pre-1.0 (`0.x`),
so the public surface (see [docs/embedding.md](docs/embedding.md)) may still
change between minor versions.

## [Unreleased]

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
