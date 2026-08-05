# Goal — EPIC-05 Backend Compatibility Kit and Release Surface

## Objective

Ship the public backend-author experience: deterministic simulator, compliance
runner/report, built-in qualification, migration guidance, and coherent
versioned release surface.

## Mandatory context

Read the complete package, all earlier handoffs, this epic's `README.md`,
existing harness compatibility/report patterns, public API stability docs,
testing docs, examples, changelog, and version/release tooling.

## Predecessor gate

- EPIC-04 duplicate/loss/restart/control/teardown matrix is green.
- Public contracts expose no undocumented internal imports.
- Local and ACP behavior is green and all no-claims are known.

## Required deliverables

- Public backend-author guide and minimal example.
- Configurable deterministic backend simulator.
- Compatibility runner and redaction-safe machine/Markdown reports.
- Local and applicable ACP qualification profiles.
- Legacy migration/deprecation guide.
- Final exports, schemas, examples, changelog, version and release evidence.

## Constraints

- No live LLM or external infrastructure requirement for compliance.
- No security certification of an external runtime.
- No passed status for skipped, unsupported, stale, or no-claim behavior.
- No internal imports in backend-author examples.

## Terminal condition

Finish only when a clean-install, public-import-only backend can run the suite,
reports are truthful and redaction-safe, the full default tests pass, and the
handoff records the exact release/version state and every remaining limitation.

