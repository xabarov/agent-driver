# SDK Refactor + Review Cycle 3 — plan

Status: planning. Date: 2026-06-09.

Purpose: after closing the E1–E8 cross-harness backlog
([improvement-plan-e1-e8](improvement-plan-e1-e8-2026-06-09.md)), do one
consolidation pass against the **complete** surface — config grouping, SDK
ergonomics, a fresh self-audit (review cycle 3), and docs/cookbook coverage.
Driven by a deep audit of three surfaces (config, SDK, CLI+docs); findings are
summarized inline with file references.

Guiding rule (unchanged): each phase ships in its own commit(s) to `origin/main`
with offline tests, black/isort, pylint 10/10 on touched modules, zero
regressions. **Backward compatibility is mandatory** — existing
`RunnerConfig(...)` / `create_agent(...)` call sites must keep working verbatim.

## Audit findings (the "why")

1. **Config sprawl.** Seven capability fields were added to `RunnerConfig`
   top-level, one per E-track: `enable_prompt_cache`, `harness_profiles`,
   `auxiliary_provider`, `auxiliary_model`, `project_memory_sources`,
   `tool_concurrency_limit`, `subagent_model_routing`
   (`runtime/single_agent/types.py`). The flat surface grows with every track;
   compaction-related flags already correctly live in `CompactionSettings`.
   The `__init__` auto-derives settings sub-objects from `_*_FIELDS` and raises
   on unknown kwargs — so grouping fields behind a settings object + delegating
   properties is **fully backward-compatible** (callers' flat kwargs still pop
   into the group). No public schema-snapshot covers `RunnerConfig` (it's not a
   pydantic contract), so moving fields breaks no snapshot test.
2. **SDK ergonomics.** Enabling the common bundle (memory + prompt-cache +
   permission + project-memory) requires wiring ~5 knobs across 3 places, and
   `tool_gate` must be repeated on **every** `run/stream/session.send` call
   (`sdk/agent.py`, `sdk/session.py`) — there is no construction-time default
   and no high-level entry point. `memory_provider` already sets the precedent
   for a construction-time capability arg (`sdk/factory.py`).
3. **Docs/cookbook gaps.** Cookbook 01–09 cover earlier features; E1/E2/E4/E5/E6
   + `eval compare` have no example. `docs/sdk.md` documents almost no config
   fields. E1–E8 lack a user-facing capabilities guide.
4. **Review-cycle-3 hygiene.** Minor: project-memory size caps are function
   defaults only (not configurable); the E3 scanner is wired only into project
   memory (skills loader + recalled memory are the noted follow-up); `suites.py`
   tasks are a hardcoded tuple; `presets.py` has one tier set.

## Phases

### R1 — Config consolidation  ·  High value · Low risk  ·  **DONE 2026-06-09**

- [x] `CapabilitySettings` (frozen dataclass, `config_sections.py`) holds the
      seven flat capability fields; `__post_init__` preserves the old
      normalization (None→empty, bool/tuple/dict coercion).
- [x] Wired into `RunnerConfig` via `_CAPABILITY_FIELDS` auto-derivation +
      delegating `@property` accessors — flat kwargs and `config.<field>` reads
      unchanged. `RunnerConfig` top-level dropped 7 fields → 1 (`capabilities`);
      it is no longer flagged for too-many-attributes.
- [x] `RunnerConfig(capabilities=CapabilitySettings(...))` supported.
- [x] Snapshot test locks the `CapabilitySettings` field set (future flat
      additions are now a deliberate choice) + flat/grouped equivalence +
      default normalization. All E1–E8 readers/callers pass unchanged.

Decision: group **all seven** into one `CapabilitySettings` (not the narrower
"model routing" split the audit floated) — one obvious home for future
capabilities, maximal reduction of the flat surface, identical back-compat. Net
effect: `RunnerConfig` top-level drops 7 fields → 1 (`capabilities`), readers
and callers unchanged.

### R2 — SDK ergonomics  ·  High value · Low risk  ·  **DONE 2026-06-09**

- [x] `create_agent(..., tool_gate=...)` (and `query(...)`) store a
      construction-time **default gate** on the `Agent`. `Agent.run` resolves
      `per-call gate if not None else default`; since `start`/`stream`/
      `stream_run` and the `Session` helpers all route through `run`, they
      inherit the default automatically — no per-method changes needed.
- [x] Per-call `tool_gate` always overrides the default.
- [x] Tests: default applies without per-call gate, per-call overrides (default
      not consulted), deny-default blocks the planned call.

Decision: did **not** add a builder/extra capability kwargs to `create_agent` —
the `capabilities=CapabilitySettings(...)` config arg from R1 plus the default
gate already cover the one-stop wiring without a heavyweight class.

### R3 — Review-cycle-3 self-audit fixes  ·  Med value · Low risk

- [ ] Wire the E3 scanner (`security/context_scan.py`) into the **skills loader**
      and **recalled long-term memory** rendering (the E3 follow-up) — same
      one-line seam used for project memory.
- [ ] Expose project-memory size caps (`max_file_chars`, `max_total_chars`) via
      `CapabilitySettings` instead of function-only defaults.
- [ ] `evals/suites.py`: allow a caller-supplied suite (keep the built-in
      general suite as default) — small factory hook, no SuiteRegistry needed.
- [ ] Optional: a second open-weight tier set / note in `presets.py`; document
      `HarnessProfile` case-insensitive `match_models` in its docstring.

Keep R3 tight — only the items that reduce real risk or close an explicit
follow-up. Defer anything speculative.

### R4 — Docs + cookbook  ·  Med value · Low risk

- [ ] Cookbook examples for the uncovered capabilities: auxiliary-model routing
      (E1), project-memory + injection scan (E2/E3), tool-arg truncation (E5),
      subagent model routing (E6), and `eval compare` / N-run aggregation (T0).
      (E4 parallel tools is largely transparent; cover briefly or fold into a
      tuning note.)
- [ ] Expand `docs/sdk.md` to document the `RunnerConfig` / `CapabilitySettings`
      fields (post-R1 surface).
- [ ] A single capabilities guide (new `docs/capabilities.md` or an `extending.md`
      section) mapping E1–E8 to the patterns they serve: cost (E1/E5/E6), safety
      (E2/E3), latency (E4), quality/eval (T0).
- [ ] Update the cookbook `README.md` table.

### R5 — CLI exposure  ·  Low value · Low risk · optional

- [ ] Optional flags for SDK-only capabilities (`--auxiliary-*`,
      `--project-memory`, `--tool-concurrency`); extend `eval compare
      --treatment` to flip the E1/E4/E6 axes, not just `prompt_cache`.

These are SDK-first capabilities; CLI exposure is convenience, lowest priority.
Do only what's cheap and clearly useful.

## Sequencing

```
R1  Config consolidation (CapabilitySettings)     ← first; everything else reads cleaner after
R2  SDK ergonomics (default tool_gate)
R3  Review-cycle-3 hygiene (scanner reuse, caps, suite hook)
R4  Docs + cookbook (E1–E8 + eval compare)
R5  CLI exposure (optional, cheap subset only)
```

R1 first so R2/R4 describe the consolidated surface. R3 is independent and can
interleave. R5 is optional.

## Non-goals / explicitly deferred

- **E7 (composite filesystem backend)** stays deferred (large, optional) — not
  part of this pass.
- No heavyweight builder/DSL unless R2 shows it reads better than `capabilities=`.
- No move of `RunnerConfig` to pydantic (the dataclass + kwargs pattern is fine;
  the unknown-kwarg `TypeError` already guards typos).

## References

- Backlog: [improvement-plan-e1-e8](improvement-plan-e1-e8-2026-06-09.md)
  (see its "Structural notes for the post-E8 pass").
- Prior cadence: [review-cycle-2](review-cycle-2-2026-06-09.md).
- Surfaces: `runtime/single_agent/types.py` (RunnerConfig),
  `runtime/single_agent/lifecycle/config_sections.py` (settings dataclasses),
  `sdk/factory.py` + `sdk/agent.py` + `sdk/session.py` (SDK), `cli/parser/`,
  `examples/cookbook/`, `docs/sdk.md` / `docs/extending.md`.
