# Package Layout Policy

This document is the source of truth for structural refactors in `agent-driver`.

## Core rule

When an implementation package exists, add new code **inside** it. Do not add
new sibling flat modules for the same concern.

Examples:

- use `agent_driver.runtime.storage.*` (not `agent_driver/runtime/storage_*.py`);
- use `agent_driver.contracts.tools.*` and `agent_driver.contracts.enums.*`;
- use `agent_driver.tools.executor.*` and package siblings (`policy`, `registry`,
  `guardrails`, `prompts`).

## Shim policy (pre-roadmap-complete)

Until completion of `docs/roadmap.md`, `agent-driver` does **not** use compatibility shims.

- When refactoring module layout, migrate internal imports, tests, and docs to new paths in the same change.
- Delete old modules instead of leaving re-export aliases.
- Treat unresolved old imports as migration bugs, not as a reason to add shims.

Compatibility aliases may be introduced only in a dedicated post-roadmap release-hardening phase.

## Scaffold packages

Create top-level phase packages only when actual implementation starts. Avoid
empty placeholder directories that are not used by code or tests.
