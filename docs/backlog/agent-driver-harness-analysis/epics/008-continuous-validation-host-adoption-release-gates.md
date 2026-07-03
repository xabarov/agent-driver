# Epic 008: Continuous Validation, Host Adoption, Release Gates

Status: completed in this checkout on 2026-07-03.

Source note: the IDE path
`excel_ai/docs/backlog/agent-driver-harness-analysis/epics/008-continuous-validation-host-adoption-release-gates.md`
was not present in this repository or its local git history. The work below was
reconstructed from the shipped harness code, tests, CLI, support-bundle
integration, and validation-artifact surfaces in this checkout.

## Outcome

Epic 008 establishes a portable, redaction-safe validation lane for host
adoption:

- Capability packs describe product scenarios, required evidence, rollout
  defaults, release gates, and adapter-owned commands without changing runtime
  behavior by default.
- `agent-driver capability-pack dry-run` resolves packs/scenarios and writes
  evidence indexes without executing commands.
- `agent-driver capability-pack run-deterministic` executes guarded
  deterministic commands, captures redacted command evidence, writes a manifest,
  and now marks `support_bundle_artifact` passed when `--output-dir` persists
  the evidence manifest.
- Validation gates are projected consistently into trace summaries, support
  bundles, CLI artifacts, and capability-pack resolutions.
- Seed adapter commands are portable: they use relative paths plus
  `AGENT_DRIVER_REPO` and adapter-owned env vars such as `EXCEL_AI_BACKEND_DIR`
  instead of embedding a developer-specific absolute checkout path.

2026-07-03 follow-up: the broader continuous-validation plane now exists on top
of the seed-pack lane:

- `agent_driver/contracts/continuous_validation.py` defines
  `ValidationRunRecord`, `HarnessBaseline`, `RegressionSummary`,
  `ReleaseGatePolicy`, `FlakeRecord`, `HostAdoptionState`,
  `ValidationArtifactRef` and `ValidationDashboardSummary`.
- `agent_driver/harness/continuous_validation.py` seeds Excel/chat-demo
  baselines, release policies, metadata-only adoption states and quarantine
  fixtures, then audits persisted evidence directories offline.
- `agent-driver capability-pack audit --evidence-index-dir <dir> --no-live
  --strict --output-dir <dir>` writes `validation_run.json` and
  `validation_report.md`.
- `stale`, `quarantined` and `no_claim` are valid validation-gate statuses; live
  provider, Phoenix, Playwright and benchmark gates remain opt-in and are not
  claimed from deterministic evidence.

## Acceptance

- [x] Capability-pack contracts exist for packs, release gates, scenarios,
  adapter manifests, evidence artifact indexes, and redaction-safe resolution.
- [x] Seed packs cover `excel_workbook_chat` and `deep_research_chat_demo`.
- [x] Optional live/provider/UI/benchmark gates default to explicit skipped
  reasons, not fake pass states.
- [x] Deterministic gate execution blocks placeholder commands and commands that
  may read environment or secret files.
- [x] CLI dry-run and deterministic execution persist validation artifacts.
- [x] Persisted deterministic runs include a standard `validation_gates.json`
  shape with `count`, `statuses`, `gates`, and `redaction`.
- [x] Host manifests do not contain `/mnt/share/...` absolute path assumptions.
- [x] Public SDK docs describe capability-pack dry-run/run-deterministic usage
  and host-manifest portability expectations.

## Verification

Focused tests run:

```bash
uv run python -m pytest tests/harness/test_capability_packs.py tests/cli/test_main.py -q
```

Result: 31 passed.

Additional continuous-validation tests run:

```bash
uv run python -m pytest \
  tests/harness/test_continuous_validation.py \
  tests/harness/test_capability_packs.py \
  tests/cli/test_main.py -q
```

Result: 40 passed.

Lint for touched continuous-validation files:

```bash
uv run ruff check \
  agent_driver/contracts/policy.py \
  agent_driver/contracts/continuous_validation.py \
  agent_driver/harness/__init__.py \
  agent_driver/harness/continuous_validation.py \
  agent_driver/cli/commands/capability_packs.py \
  agent_driver/cli/parser/builder.py \
  tests/harness/test_continuous_validation.py \
  tests/cli/test_main.py
```

Result: passed.

Default sweep run:

```bash
uv run python -m pytest -q
```

Result: passed with the repository default marker selection. One existing
Starlette/httpx deprecation warning remains in `tests/adapters/test_a2a.py`.

## Follow-Up Watchlist

- Add non-seed adapter manifest loading when host projects are ready to own
  their manifests outside agent-driver code.
- Decide whether `support_bundle_artifact` should require a full runtime support
  bundle for live releases, while keeping CLI validation manifests sufficient
  for deterministic pack evidence.
- Add a strict release command that combines deterministic pack execution with
  policy-supervision artifact audit when live claims are made.
