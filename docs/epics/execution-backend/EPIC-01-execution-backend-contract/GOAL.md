# Goal — EPIC-01 Public ExecutionBackend Contract

## Objective

Implement the smallest supported `ExecutionBackend` contract, local
compatibility backend, and runner injection seam that route governed command
and initial file operations without changing default local behavior.

## Mandatory context

Read, in order:

1. `CLAUDE.md`
2. `docs/epics/execution-backend/README.md`
3. `docs/epics/execution-backend/TARGET_CONTRACT.md`
4. `docs/epics/execution-backend/EPIC_STANDARD.md`
5. this epic's `README.md`
6. `docs/embedding.md`, `docs/sdk-tools.md`, `docs/builtin-tools.md`, and
   `docs/acp.md`
7. the baseline code paths listed in this epic

## Predecessor gate

- Record current commit/version/worktree.
- Run focused existing tests for runner workspace context, built-in shell,
  built-in filesystem tools, governed execution, and ACP routing.
- Stop if failures indicate an unexplained baseline regression.

## Required deliverables

- Public validated execution contracts and async protocol.
- Supported host injection surface.
- Local compatibility backend and minimal deterministic fake.
- Governed `bash` plus initial file routing.
- ACP compatibility/migration.
- Contract/export/schema tests, runtime acceptance tests, docs, example,
  changelog and SemVer decision.

## Constraints

- No concrete remote/container dependency.
- No second agent loop or policy engine.
- No model-controlled backend selection.
- No accidental rename or reuse of existing workspace/lease concepts.
- No untyped public result dictionaries.

## Terminal condition

Finish only when the epic Definition of Done is satisfied and the handoff names
the exact test commands, results, commit/version state, limitations, and the
green predecessor gate for EPIC-02.

