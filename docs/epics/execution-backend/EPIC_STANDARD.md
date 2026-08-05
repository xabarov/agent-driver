# Execution-Backend Epic Standard

Use this standard for every epic in this package.

## One goal at a time

Start only the selected epic. Verify its predecessor gate before editing code.
Do not fold a later epic into the current change because its types appear
convenient. A minimal forward-compatible placeholder is acceptable only when
the selected epic's public contract requires it.

## Entry audit

Before implementation:

1. Confirm branch, commit, version, remotes, and clean/dirty worktree.
2. Read repository `CLAUDE.md`, package `README.md`, `TARGET_CONTRACT.md`, and
   the selected epic's `README.md` and `GOAL.md` in full.
3. Inspect current implementations named in the epic. Do not assume the
   baseline in these docs is still exact.
4. Search for concurrent user changes and preserve unrelated work.
5. Restate the public API and wire-contract delta, migration impact, and SemVer
   class before editing.

## Implementation rules

- Extend the existing runner, governed tool executor, context scopes, runtime
  events, stores, artifacts, and public facades. Do not build a second agent
  loop or a parallel policy system.
- Keep backend selection server/host-derived and outside model-visible tool
  arguments.
- Use validated contracts for durable or cross-process data. Avoid new
  free-form `app_metadata` protocols.
- Keep current local and ACP behavior green unless the epic explicitly defines
  a documented migration.
- Use async interfaces at remote I/O boundaries and preserve strict async test
  behavior.
- Bound and redact all remote text before it reaches logs, events, checkpoints,
  traces, prompts, or exception messages.
- Never claim a cancellation, durability, teardown, or capability guarantee
  that the implementation cannot prove.
- Keep domain policy and concrete infrastructure outside Agent Driver.

## Required tests

Each epic includes focused acceptance scenarios. In addition:

- add contract round-trip and schema snapshots for public models;
- update public export snapshots for every supported facade change;
- test both configured-backend and unchanged local behavior;
- test cancellation/timeout and at least one injected backend failure;
- assert redaction and output bounds on events, errors, and snapshots;
- run the narrow changed-area suite while iterating;
- run the full default test suite before closing a public runtime change;
- run formatting/import checks and `pylint` over materially changed modules.

Live external infrastructure is not required to prove Agent Driver semantics.
Use the deterministic fake/backend simulator. A separate backend package may
run the same compliance scenarios against real infrastructure.

## Documentation and compatibility

Before closing:

- update `docs/embedding.md`, relevant runtime/tool docs, and a runnable example;
- update the changelog and migration/deprecation notes where needed;
- classify SemVer impact and synchronize package metadata if the repository's
  release discipline requires a bump;
- record any deliberately unsupported capability as `unsupported`, not an open
  TODO hidden behind a successful status;
- add a dated handoff containing commit, exact tests, skipped/live checks,
  known limitations, and the next predecessor gate.

## Definition of done

An epic is complete only when:

1. Its acceptance scenarios pass through the real runner and governed tool
   path, not only direct protocol mocks.
2. The public contract, runtime behavior, event/trace output, recovery behavior,
   and docs agree.
3. Existing local and ACP users have a tested compatibility path.
4. The worktree contains no unexplained changes.
5. The final handoff names evidence and limitations without overstating them.

