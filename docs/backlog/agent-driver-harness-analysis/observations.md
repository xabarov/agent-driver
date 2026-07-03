# Cross-Cutting Observations

Date: 2026-07-03

## Source Availability

The IDE referenced `excel_ai/docs/backlog/agent-driver-harness-analysis/...`,
but this checkout only contains `docs/` under `agent-driver`; the original 008
epic and checklist files were not present in the working tree, `git ls-files`,
or local git history. The backlog records in this directory were reconstructed
from the code and tests that are present.

## Host Portability

Capability-pack adapter manifests are part of the release interface for host
adoption. They should not encode a contributor's local checkout paths. Use
relative commands from `AGENT_DRIVER_REPO` and adapter-owned env vars for sibling
projects or interpreters. This keeps dry-run output shareable and makes
deterministic runs repeatable on CI and host machines.

## Gate Semantics

Do not treat a skipped optional gate as release evidence. A skipped gate needs a
reason that says why no claim is being made. Required gates should remain
`not_run`, `blocked`, or `failed` until evidence exists.

For deterministic capability-pack runs, the persisted validation-artifact
manifest is acceptable evidence for `support_bundle_artifact` in the inert seed
slice. Live releases may still require a full runtime support bundle plus
policy-supervision audit.

## Artifact Shape

`validation_gates.json` should use the same shape everywhere:

- `count`
- `statuses`
- `gates`
- `redaction`

Raw `{"gates": [...]}` files are harder for support tooling and host release
scripts to consume consistently.

## Test Drift Around Budget Limits

Some tests still expected endless tool-call loops to fail with
`tool_policy_denied`. The current runtime contract is more nuanced: tool loops
are bounded, but the default path can finish through a forced/final-answer
terminal before exceeding the hard `tool_calls > max_tool_calls` failure check.
Tests that need a pure failed cap should explicitly configure
`RunnerConfig(budget_grace_enabled=False)` and use a scenario that actually
exceeds the hard limit.

## Metadata Inventory Discipline

The runtime metadata inventory is part of the validation surface. Streaming and
provider fallback diagnostics such as `assistant_stream_events_seen`,
`assistant_stream_token_chunks_seen`, `last_provider_diagnostics`, and
`provider_stream_non_stream_fallback` must be documented when literal
`context.metadata` keys are introduced.

## Continuous Validation Semantics

The continuous-validation layer should remain an offline evidence aggregator
first. `agent-driver capability-pack audit` consumes persisted
`evidence_index.json` and `manifest.json` rows, verifies sizes/checksums, and
writes validation JSON/Markdown reports. It must not infer live/provider/UI or
benchmark success from deterministic artifacts. In `--no-live` mode, unexecuted
OpenRouter, Phoenix, Playwright and benchmark gates should become `no_claim` or
`stale`, not `passed`.

## Repository Inventory Limits

Large, unbounded repository scans can fail before they provide evidence. During
the 2026-07-03 continuous-validation implementation, broad parallel scans over
`.git`, virtualenvs, caches and generated reports hit "Too many open files".
Future evidence inventory should target known artifact roots first and exclude
generated directories before treating scan errors as missing evidence.

## Provider Catalog Status Vocabulary

Provider compatibility contracts and validation gates intentionally use
different status vocabularies. Provider facts use `supported`, `degraded`,
`blocked`, `cache_hit` and `no_claim`; validation gates use `passed`, `failed`,
`blocked`, `skipped`, `stale` and `no_claim`. New provider-catalog reports must
translate between those vocabularies at artifact boundaries instead of reusing
provider fact statuses as gate statuses.

## Provider Artifact Type Wiring

When adding a new provider evidence artifact type, wire it through all artifact
contracts together: `EvidenceArtifactRef`, `ValidationArtifactRef`,
continuous-validation known artifact mapping, and gate-id mapping. Updating only
one layer produces artifacts that write successfully but fail strict 008 audit.
