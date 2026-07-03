# Checklist 008: Continuous Validation, Host Adoption, Release Gates

Status: done for the current inert seed-pack slice.

## Capability Pack Contract

- [x] Define redaction-safe pack, release gate, scenario, adapter manifest, and
  evidence-index contracts.
- [x] Reject secret-shaped metadata values while allowing secret env var names.
- [x] Keep pack selection inert by default with `no_runtime_behavior_change`.
- [x] Represent required gates separately from optional live/provider/UI gates.

## Host Adoption

- [x] Seed Excel and chat-demo adapter manifests with owner notes, expected
  evidence, env var names, and command references.
- [x] Remove developer-local absolute path assumptions from seed commands.
- [x] Use `AGENT_DRIVER_REPO` for agent-driver checkout location.
- [x] Use adapter-owned env vars for external host checkout or interpreter
  locations, for example `EXCEL_AI_BACKEND_DIR` and `EXCEL_AI_BACKEND_PYTHON`.
- [x] Document the host-manifest portability rule in SDK docs.

## Release Gates

- [x] Project default gate statuses into support bundles and run trace summaries.
- [x] Preserve skipped-gate reasons for optional live/provider/UI/benchmark gates.
- [x] Run deterministic commands behind simple command guards and timeouts.
- [x] Persist `manifest.json`, `evidence_index.json`, `validation_gates.json`,
  command outputs, and capability-pack payloads from CLI execution.
- [x] Mark `support_bundle_artifact` passed only when `--output-dir` causes the
  validation manifest to be persisted.
- [x] Add offline audit over persisted evidence indexes with strict mode,
  no-live `no_claim` semantics and checksum/size validation.
- [x] Generate continuous-validation JSON/Markdown reports with product rows,
  shared gate rows and skipped/no-claim live gates.
- [x] Keep quarantine explicit: active flakes show as `quarantined`; expired
  flakes fail instead of becoming permanent blind spots.

## Tests

- [x] Cover seed pack validation for Excel and chat-demo.
- [x] Cover secret-value rejection and env-var-name allowance.
- [x] Cover dry-run and deterministic CLI artifact persistence.
- [x] Cover blocked placeholder commands.
- [x] Cover absence of `/mnt/share/...` path assumptions in seed manifest
  command fields.
- [x] Cover standard `validation_gates.json` status projection for deterministic
  CLI runs.
- [x] Cover continuous-validation contracts, seed baselines, gate policies,
  metadata-only adoption states, missing artifact refs, strict missing/corrupt
  evidence and quarantine behavior.
- [x] Run the repository default pytest sweep after closing stale test
  expectations around budget-limited tool loops and runtime metadata inventory.
