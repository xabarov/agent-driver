# Refactoring plan — 2026-06-10

Status: planned. Scope: structural cleanup after the n7 platform-adapters work
(ACP / OpenAI HTTP / MCP / A2A). **Behavior-preserving only** — every item keeps
the full suite green (currently 2168 passed) and changes no public/wire
behavior. No feature work here.

## How this plan was produced (provenance)

A read-only idea-mining pass over the repo (72.6k LoC, 443 files):

- **Size/hotspot scan** (`wc -l` by package + per file): runtime (19.3k), tools
  (12.4k), cli (7.2k), observability (5.1k) dominate. Largest single files:
  `observability/run_trace/summary.py` (2316), `cli/evals.py` (2149),
  `runtime/single_agent/tool_stage/__init__.py` (1966), `tools/builtin/web.py`
  (1245), `tools/executor/governed.py` (1073).
- **Duplication pass over the new network surfaces** (`server/`, `adapters/acp`,
  `adapters/a2a`, `mcp_server/`) — code we just wrote, highest confidence.
- **Shim / dead-code pass** keyed off `scripts/check_package_layout.py`, which
  currently **fails** on ~26 "shim without removal date" files.
- **Large-module pass** assessing god-modules + split feasibility + call-site
  risk.

Each finding below cites the concrete sites. Where seeming-duplicates actually
differ on purpose, that's called out (don't "fix" them).

---

## Tier A — quick wins (low risk, mechanical, high signal)

### A1. Pass the package-layout check (shim hygiene)

`scripts/check_package_layout.py` fails because ~26 backward-compat re-export
shims lack a `SHIM-REMOVE-BY:` date. Two moves:

- **Delete the 11 zero-import shims** (no module in the repo imports them):
  `llm/providers_impl/openai_compatible_normalization.py`,
  `…/openai_compatible_payload.py`, the six
  `observability/run_trace_*.py` shims, and
  `runtime/single_agent/{continuation,journal,output_builders,resume,steps}.py`.
  Verify zero importers (`grep -rn "import <stem>"`) before each delete.
- **Add `SHIM-REMOVE-BY: 2026-12-01`** to the remaining shims that *are* still
  imported (notably `runtime/single_agent/config_sections.py` — 16 importers —
  and `runtime/single_agent/llm.py` — 26 importers — plus the ~13 light-use
  ones). These stay as re-exports; the date just satisfies the checker and
  records intent.

Outcome: `check_package_layout.py` exits 0; ~11 dead files removed. Risk: LOW
(guarded by import grep + full suite).

### A2. Unify the network-surface duplication (`server/` + adapters)

Real, byte-level duplication across the 4 surfaces we just built:

- **SSE framing** — `f"data: {json.dumps(...)}\n\n"` + `"data: [DONE]\n\n"` is
  hand-built in `server/app.py` (chat stream, runs events, responses stream),
  and `adapters/a2a/http.py`. → Extract `server/sse.py`:
  `sse_data(payload)`, `sse_event(event, data)`, `SSE_DONE`.
- **JSON-RPC envelope + error codes** — `_ok`/`_err` are duplicated verbatim in
  `mcp_server/server.py:246-255` and `adapters/a2a/server.py:174-183`; error
  codes (`-32700/-32600/-32601/-32602`) are re-declared in 4 files. → Extract
  `server/jsonrpc.py`: `ok(id,result)`, `error(id,code,msg)`, and the standard
  code constants; import in MCP + A2A core/http.
- **Bounded-LRU store** — identical `OrderedDict` + `move_to_end` +
  `popitem(last=False)` in `OpenAIServer._sessions`, `ResponseManager._store`,
  `A2aServer._tasks`. → Extract `server/bounded_store.py::BoundedLruStore`
  (get/set/delete). NOTE: `RunManager._runs` evicts by *terminal status*, not
  pure LRU — leave it as is (genuinely different).
- **Usage extraction** — `translate.usage_dict` + inline copies in `runs.py`.
  Consolidate to one helper. **Keep two variants on purpose:** chat/runs use
  `prompt_tokens`/`completion_tokens`; `/v1/responses` uses
  `input_tokens`/`output_tokens` (OpenAI's two surfaces genuinely differ — this
  is not a bug). Put both in `server/usage.py` with clear names.
- **Auth gating** — `if not is_authorized(...): return …401` repeats ~9× in
  `server/app.py`. MCP already wraps it (`_authorized`). → Add an
  `OpenAIServer._authorized(request)` helper (or a small route decorator) and
  collapse the call sites. Low value but cheap.

**Do NOT consolidate** the text/parts flatteners (`schema._flatten_text`,
`a2a._text_from_parts`, `acp._prompt_text`) — they decode three different wire
formats (OpenAI dict-parts / A2A kind-parts / ACP block objects).

Risk: LOW — these are leaf helpers with thorough offline tests on each surface.

---

## Tier B — module splits (low risk, high maintainability win)

Behavior-preserving extractions; public entry points stay put, internals move.

### B1. `runtime/single_agent/tool_stage/__init__.py` (1966 → ~600 + 3 modules)
The orchestrator carries ~900 lines of **deep-research-specific** coercion /
repair / artifact-write helpers. Extract into the existing `tool_stage/` package:
`deep_research_coercion.py`, `artifact_writes.py`, `transitions.py`. Keep
`execute_tool_stage_step` (the only import, from `steps.py`) in `__init__`.
Risk: LOW (2 call sites, all moved fns are private). **Highest win/risk ratio.**

### B2. `tools/builtin/web.py` (1245 → 4 modules)
Five independent tools in one file. Split `web_fetch.py` / `web_search.py` /
`web_content.py` (pdf/source/browser) / `web_common.py` (HTML + URL utils);
`register_web_tools` (the only public export, 1 call site) imports from them.
Risk: LOW (registration-time only).

### B3. `tools/executor/governed.py` (1073 → +1 module)
Extract the 8 arg-normalization/coercion helpers (`_normalize_tool_alias`,
`_coerce_json_string_args`, `_normalize_tool_args`, …) into
`executor/normalization.py`; keep `GovernedToolExecutor` in `governed.py`.
Risk: LOW (only `GovernedToolExecutor` is exported). Note this is a hot path —
behavior must be identical; rely on the existing executor tests.

---

## Tier C — bigger splits (medium risk; do deliberately, one at a time)

### C1. `observability/run_trace/summary.py` (2316 → façade + 4 signal modules)
Extract `research_signals.py` / `subagent_signals.py` / `python_signals.py` /
`artifact_signals.py`; `summary.py` becomes a thin façade that imports + keeps
back-compat re-exports (12 test files import internals). Risk: MEDIUM.

**Done 2026-06-23.** `summary.py` 2316 → 604 (façade keeps `summarize_run_trace`
+ control/notes/runtime-marker helpers). Helpers split into `_common.py` (238,
shared primitives + constants), `python_signals.py` (62), `artifact_signals.py`
(272), `subagent_signals.py` (388), `research_signals.py` (887). Import DAG is
acyclic (`_common` ← `artifact_signals` ← `subagent_signals` ← `research_signals`)
— `_common` breaks the subagent↔research cycle by owning the child-evidence and
deep-research-contract primitives both clusters need. No external module imports
internals (the "12 test files" reference was stale), so no re-export shim was
needed beyond `__all__ = ["summarize_run_trace"]`. Suite unchanged (2219
outcomes, green); pylint clean (no undefined/unused-import/cyclic-import).

### C2. `cli/evals.py` (2149 → providers / scenarios / scoring / reporting)
Split fake providers, scenario defs, scoring, and reporting; keep the harness +
`EvalSummary` in `evals.py` with re-exports. Risk: MEDIUM-HIGH (10+ call sites,
test imports across parts). Test-time only, so no runtime risk.

**Done 2026-06-23.** `evals.py` 2149 → 735 (harness: `run_live_evaluation`,
`_run_eval_scenario*`, `summarize_run`, `EvalSummary`, retry/merge, plus a
re-export block). The `_run_eval_scenario` chain deliberately stays in `evals.py`
so `monkeypatch.setattr(evals_module, "_run_eval_scenario", …)` in
`tests/cli/test_eval_harness.py` keeps hitting the call inside
`_run_eval_scenario_with_retry`. Parts extracted to `eval_providers.py` (255,
four fake providers), `eval_scenarios.py` (900, `EvalScenario` + `default_*` +
suite membership), `eval_scoring.py` (117, answer-matching + bug tags),
`eval_reporting.py` (206, render + artifact writers). DAG is acyclic
(`eval_scenarios` is the leaf type owner; scoring/reporting → scenarios;
harness → all parts). `EvalSummary` stays in the harness, so `eval_reporting`
references it only under `TYPE_CHECKING` (annotation-only) to avoid a cycle.
`evals.py` re-exports every moved name (public `__all__` + the private
`_EvalGamma*` / `_answer_matches_expectations` / `_write_scorecard` the tests
import). Suite unchanged (2219 outcomes, green); pylint message categories
identical to the original minus the dropped `too-many-lines`.

### C3 (DEFER). Research modules — `runtime/research_session_contract.py` (986)
and `runtime/single_agent/research/gating.py` (958). High coupling + mutual
imports → real circular-import risk. Worth splitting but **defer** until the
above land and there's a reason to touch them.

`subagents/executor.py` (1064): assessed as a single cohesive concern — **no
split**, just keep internal docs.

---

## Sequencing

1. **Tier A** first — A1 (shim hygiene → green layout check) then A2
   (server/adapters de-dup). Small, independent, immediately valuable.
2. **Tier B** — B1 (tool_stage) → B2 (web) → B3 (governed). Each its own commit,
   full suite green between.
3. **Tier C** — only if/when the maintenance pain justifies the churn; C3
   deferred.

## Guardrails (every item)

- Behavior-preserving: no public API, CLI, or wire change. Where a moved symbol
  was imported elsewhere, keep a re-export (or update importers in the same
  commit).
- Full suite (`pytest`, 2168 passing) green after each commit; `black`/`isort`
  applied; `scripts/check_package_layout.py` exits 0 after A1.
- One concern per commit, so each is reviewable and revertable.
