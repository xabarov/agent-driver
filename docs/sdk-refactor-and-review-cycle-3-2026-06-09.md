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

### R3 — Review-cycle-3 self-audit fixes  ·  Med value · Low risk  ·  **DONE 2026-06-09**

- [x] Wired the E3 scanner into **recalled long-term memory** (`render_recall_block`
      scrubs each record, substituting a placeholder on a hit) and **untrusted
      skills** (`view_skill` scans non-`trusted` skill bodies; trusted, author-
      controlled skills pass through). Closes the E3 follow-up.
- [x] Exposed project-memory size caps via `CapabilitySettings`
      (`project_memory_max_file_chars` / `project_memory_max_total_chars`),
      threaded into the prompt-build loader; snapshot test updated.
- [x] Documented `HarnessProfile` case-insensitive `match_models` in its docstring.
- [x] Tests: recall-block scrub, untrusted-skill withhold, trusted/clean
      passthrough.

Not done (already satisfied / deferred): `evals/suites.py` extensibility —
`run_comparison`/`BatchRunner` already accept any caller-supplied `list[BatchItem]`,
so the built-in `general_task_suite()` is just the default; no extra hook needed.
A second `presets.py` tier set deferred (no demand yet).

### R4 — Docs + cookbook  ·  Med value · Low risk  ·  **DONE 2026-06-09**

- [x] Cookbook examples 10–13: `10_capabilities.py` (CapabilitySettings +
      prompt-cache + default tool_gate), `11_project_memory.py` (E2/E3 load +
      injection scan), `12_subagent_routing.py` (E6), `13_eval_compare.py` (T0
      baseline-vs-treatment). All run offline + pass the cookbook smoke test.
- [x] `docs/sdk.md` gained a "Capabilities" section documenting the
      `RunnerConfig` / `CapabilitySettings` fields + the construction-time gate.
- [x] `docs/extending.md` gained a "Capability map" (goal → knob → example)
      instead of a separate doc — centralizes the index. `CapabilitySettings`
      re-exported from `agent_driver.runtime` for discoverability.
- [x] Cookbook `README.md` table updated.

(E5 tool-arg truncation / E4 parallel tools are transparent runtime behaviors —
documented in the capability map + operational thresholds rather than a contrived
example.)

### R5 — CLI exposure  ·  Low value · Low risk · optional  ·  **DONE 2026-06-09 (scoped)**

- [x] Extended `eval compare --treatment` beyond `prompt_cache` to
      `tool_arg_truncation` and `tool_concurrency` (serial↔parallel) — the axes
      that flip cleanly off/on over the general suite. An axis→config map keeps
      it extensible.
- [x] Tests: each axis runs offline with the right labels; unknown axis
      rejected by argparse.

Scoped out (intentional): per-capability `run`/`chat` flags for `--auxiliary-*`,
`--project-memory`, etc. — SDK-first, low value, and they would grow the flag
surface we just consolidated. `eval compare` axes for E1 (auxiliary, needs a
second provider) and E6 (subagent routing, needs subagents) are left SDK-only —
the general suite can't exercise them as a binary toggle.

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
