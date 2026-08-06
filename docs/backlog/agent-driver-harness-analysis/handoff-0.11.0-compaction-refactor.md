# Release handoff — agent-driver 0.11.0 (compaction epic + hot-path refactor)

Date: 2026-08-06. Version `0.11.0` is a **backward-compatible MINOR** over `0.10.0`.
It is an internal-quality release: additive, opt-in compaction fixes plus a large
behavior-preserving refactor pass. No public embedding surface was removed or changed,
no store/migration is required, and defaults are unchanged — any host on `0.3.3`
through `0.10.0` upgrades in place.

## Release identity

- Package version: `0.11.0`.
- Release source commit: `29f9297320796e4a1d3aa62e8b82e5eec6831a1f`.
- Upstream base: `187370d` (the merged refactor sweep tip).
- Public remote: `https://github.com/xabarov/agent-driver`.
- Git tag: `v0.11.0` (annotated, pushed to `origin`).
- Release source status: clean (`git status --porcelain` empty at build time).
- Supported Python: `>=3.11`; CI exercises Python 3.11 and 3.12.

## What is in this release

Two workstreams, both landed on `origin/main` as their own reviewed increments:

1. **Compaction-improvement epic** (research-first; see
   `docs/epics/compaction-improvement/`). Budget correctness — an unresolved model id
   now falls back to a modern 128K window instead of silently assuming 12K (BUG-2), the
   summariser char cap is a fraction of the resolved window instead of a fixed 262144
   ceiling (BUG-1), and the retention predicate no longer drops evidence-only messages
   (BUG-4, a data-loss fix); calibrated chars/token from real provider usage (BUG-6);
   a window-relative `compact_ratio` pressure net (BUG-3); the condenser-pipeline
   foundation (Option B1a, additive, not yet wired); and the **`compaction_model`
   "default" sentinel 400 fix** — a host that enabled `llm_compaction` without naming a
   model previously 400'd on the first compaction; the sentinel now resolves to the
   run's own model.
2. **Refactor pass** (behavior-preserving). Dedup + dead-code cleanup, god-MODULE
   splits (governance / skills / research-session-contract into re-export shims that
   keep every import and `__all__` intact), and a god-FUNCTION sweep — eleven runtime
   hot-path functions decomposed into named helpers (lifecycle finalize; the tool
   executor's one-call + allow-path; the compaction stage's dispatch + llm-full +
   session-memory paths; the llm_step call/build/complete/forced-final-recovery
   functions; the run-trace summariser; deterministic trimming). No control-flow or
   behavior change.

Full detail in `CHANGELOG.md` under `[0.11.0]`.

## Exact wheel

- Filename: `agent_driver-0.11.0-py3-none-any.whl`.
- Size: `1278118` bytes.
- SHA-256: `ac854396f3b3284a870a41e09d3bb28f03d25686948ab539944feaf45f36c36d`.
- `SOURCE_DATE_EPOCH`: `1786044990` (the release commit timestamp).
- Release builder: CPython `3.12.3`, `setuptools==83.0.0`, `wheel==0.47.0`.
- METADATA: `Name: agent-driver`, `Version: 0.11.0`, `Requires-Python: >=3.11`.

The prior `agent_driver-0.4.0-py3-none-any.whl` artifact is **retained** alongside the
new wheel in the (git-ignored) `dist/` directory — this release adds an artifact, it
does not replace the earlier one. `dist/` is out-of-band; wheels are not committed to
git, so consumers pin by the wheel identity above, not by a repository path.

## Compatibility

- **Public surface: unchanged.** Every extracted helper in the refactor pass is a
  private (`_`-prefixed) function; the god-module splits are full re-export shims that
  preserve each module's imports and `__all__`. No public embedding contract
  (`docs/embedding.md`) symbol was removed, renamed, or re-signatured.
- **No migration.** No database, persistence-store, approval-store, provider-adapter,
  or execution-backend migration is required. All compaction knobs are opt-in and
  default off/unchanged; a host that passes no `CompactionSettings` sees identical
  behavior to `0.10.0`.
- **Upgrade path.** A consumer pinned anywhere from `0.3.3` to `0.10.0` can move to
  `0.11.0` in place. The execution-backend surface added across `0.4.0`–`0.10.0` (with
  its compatibility kit and pre-1.0 deprecation window — see
  `docs/execution-backend-migration.md`) is untouched here.
- **One intentional behavior change, narrow blast radius:** BUG-2's window fallback
  (12K → 128K) only affects hosts that leave the context window *under-configured* AND
  do not set `context_window_estimate`; it fires a once-per-run
  `context_window_unresolved_fallback` warning so the condition is loud. Hosts that set
  the window explicitly (excel-ai, PentestLens) are unaffected.

## Verification (local)

- Full test suite: **3249 passed**, 78 deselected, 6 xfailed (`.venv` pytest,
  `--import-mode=importlib`).
- `test_version`: green — `agent_driver.__version__ == 0.11.0 == pyproject` after the
  egg-info refresh.
- `ruff check` clean on every file touched this cycle.

> **CI is the authoritative gate and has not yet been confirmed for this commit.**
> Local "all green" has hidden clean-CI failures before (missing extras / ruff drift /
> exec bits / 3.11 asyncio). Before a consumer pins `0.11.0`, confirm the GitHub
> Actions run on `29f9297` (tag `v0.11.0`) is green across all mandatory jobs (test
> 3.11/3.12, lint, type, docs, real-Postgres, reproducible-wheel).

## Consumer pin

```text
Agent Driver release version: 0.11.0
Release source commit SHA: 29f9297320796e4a1d3aa62e8b82e5eec6831a1f
Git tag: v0.11.0
Wheel filename: agent_driver-0.11.0-py3-none-any.whl
Wheel size: 1278118 bytes
Wheel SHA-256: ac854396f3b3284a870a41e09d3bb28f03d25686948ab539944feaf45f36c36d
SOURCE_DATE_EPOCH: 1786044990
Public remote: github.com/xabarov/agent-driver
Build toolchain: CPython 3.12.3 / setuptools 83.0.0 / wheel 0.47.0
Prior artifact retained: agent_driver-0.4.0-py3-none-any.whl
```

Pin the release source commit above, not floating `main` or this later documentation
commit.

## Residual notes

- The wheel was built once locally; a second reproducible build under CI's pinned
  toolchain should be confirmed byte-identical before the pin is treated as immutable
  (the prior handoffs record two byte-identical builds; this one records one).
- Compaction remains **dormant in the excel-ai flagship by design** (it ships no
  `CompactionSettings`; see the SSB on-vs-off measurement in
  `docs/epics/compaction-improvement/MEASUREMENT-excelai-ssb-ab.md`). The B1a condenser
  pipeline and B2 amortized summary optimize a path the flagship does not currently
  exercise; they carry no obligation for this release.
