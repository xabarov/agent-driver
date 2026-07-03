# Provider plugin, catalog and model-routing plane

Дата создания: 2026-07-03.

Статус: completed for the deterministic/no-live provider-catalog slice in this
checkout on 2026-07-03.

Source note: the IDE path
`excel_ai/docs/backlog/agent-driver-harness-analysis/epics/012-provider-plugin-catalog-model-routing-plane.md`
was not present in this `agent-driver` checkout or local git history. This
implementation record was reconstructed from that product-backlog epic and the
code shipped in this repository.

## Outcome

Epic 012 adds an offline-first provider plugin/catalog/model-routing evidence
plane above `ProviderDescriptor`. The descriptor layer still owns construction;
the new provider catalog layer owns deterministic compatibility metadata,
catalog fixtures, sanitizer verdicts, route plans, and validation artifacts.

Implemented command:

```bash
uv run agent-driver provider-catalog audit \
  --scenario provider_catalog.sanitizer_matrix.v1 \
  --no-live \
  --output-dir .agent-driver/provider-catalog/epic-012-deterministic
```

Persisted artifacts:

- `.agent-driver/provider-catalog/epic-012-deterministic/provider_compatibility_report.json`
- `.agent-driver/provider-catalog/epic-012-deterministic/provider_compatibility_report.md`
- `.agent-driver/provider-catalog/epic-012-deterministic/provider_catalog.json`
- `.agent-driver/provider-catalog/epic-012-deterministic/provider_sanitizer_matrix.json`
- `.agent-driver/provider-catalog/epic-012-deterministic/evidence_index.json`
- `.agent-driver/provider-catalog/epic-012-deterministic/validation_gates.json`
- `.agent-driver/provider-catalog/epic-012-deterministic/audit/validation_run.json`
- `.agent-driver/provider-catalog/epic-012-deterministic/audit/validation_report.md`

## Completed Scope

### Phase A. Provider surface inventory

- [x] Inventory current provider descriptors, env alias handling and
  construction behavior via `provider_descriptors.py` bridge metadata.
- [x] Inventory current capability/profile inference and route preflight logic
  via bridged `ProviderCapabilityProfile` and `ProviderRouteProfile`.
- [x] Inventory OpenAI-compatible payload/request-shaping branches through the
  sanitizer matrix.
- [x] Inventory router health/fallback as evidence-only routing plan inputs,
  without changing default route selection.
- [x] Inventory OpenRouter preflight artifacts and Phoenix gate behavior as
  deterministic preflight reports plus no-claim live gates.
- [x] Inventory Excel AI provider needs in
  `provider_catalog.excel_workbook_routes.v1`.
- [x] Inventory chat-demo provider needs in
  `provider_catalog.chat_demo_research_routes.v1`.
- [x] Mark reference-project features as adapted into contracts and no-claim
  rows where current evidence is insufficient.

### Phase B. Plugin/catalog contracts

- [x] Added provider plugin manifest, route plugin, model catalog, catalog fetch
  plan, capability matrix, sanitizer fixture, routing plan, preflight report,
  and compatibility report contracts.
- [x] Added status vocabulary:
  `supported`, `unsupported`, `degraded`, `blocked`, `skipped`, `stale`,
  `no_claim`, `failed`, `cache_hit`, `cache_stale`.
- [x] Added JSON/redaction validation for metadata, catalogs, and reports.
- [x] Added version/source/freshness/checksum fields for catalogs and fixtures.
- [x] Added tests rejecting secret-shaped metadata and raw provider responses.

### Phase C. Built-in plugin registry and bridge

- [x] Implemented deterministic in-process provider plugin registry.
- [x] Added built-in manifests for `openai`, `openrouter`, `vllm`,
  `anthropic`, `deepseek`, `glm_zai`, `gemma_gemini`, and `ollama`; uncertain
  rows are `no_claim`.
- [x] Bridged current `ProviderDescriptor`, `ProviderCapabilityProfile`, and
  `ProviderRouteProfile` into report shapes.
- [x] Added authoring surface through manifest/registry helpers and CLI output.
- [x] Added tests for duplicate ids, aliases, replacement, and fallback rows.

### Phase D. Request sanitizer and preflight matrix

- [x] Generalized deterministic request-shaping preflight beyond OpenRouter in
  `ProviderPreflightReport`.
- [x] Added sanitizer fixtures for max-token field, forced tool choice, strict
  JSON schema, reasoning/thinking, reasoning echo-adjacent metadata, parallel
  tools, vision/tool-message metadata, and unsupported fields.
- [x] Added fixture rows for OpenAI, OpenRouter, vLLM/local, DeepSeek,
  GLM/Z.AI, Gemma/Gemini, Anthropic/Sonnet, and Ollama.
- [x] Added tests proving unsupported features downgrade/block/no-claim instead
  of silently passing.
- [x] Added compact support metadata helper for selected route profile,
  sanitizer verdict, and downgrade reasons.

### Phase E. Model catalog cache

- [x] Defined file-backed deterministic catalog cache shape with source,
  freshness, checksum, and redaction status.
- [x] Added deterministic catalog fixtures for built-in providers.
- [x] Added optional fetch plans as `fetch_skipped`/`no_claim`; no network is
  required.
- [x] Added cache/no-claim behavior without network access.
- [x] Added tests proving deterministic compatibility does not require live
  catalog fetch.

### Phase F. Routing plan integration

- [x] Added `ProviderRoutingPlan` generation from requested capabilities:
  tools, structured output, reasoning, long context, vision, source tools,
  report artifacts, and live gates.
- [x] Bridged router health/fallback status into routing-plan inputs without
  changing default route selection.
- [x] Emitted route selected, downgraded, blocked/no-route, and switch-opt-in
  metadata in deterministic reports.
- [x] Covered fallback eligibility/no-route-style behavior in plan tests.
- [x] Kept automatic route switching opt-in and evidence-only.

### Phase G. Host validation: Excel AI

- [x] Added deterministic Excel AI provider compatibility route report.
- [x] Modeled workbook chat routes for tools, structured chart/report output,
  workbook context, screenshot/vision, and long context.
- [x] Provider routing plans explain selected/downgraded/no-claimed routes
  without Excel-specific upstream provider branches.
- [x] Live provider and benchmark claims remain `no_claim`.
- [x] Playwright screenshots are `no_claim`; no user-visible Excel UI behavior
  changed in this slice.

### Phase H. Host validation: chat-demo deep research

- [x] Added deterministic chat-demo provider compatibility route report.
- [x] Modeled deep-research routes for source tools, report artifacts, long
  context, structured output, and live probe model selection.
- [x] Routing plans explain route selection and downgrades in report artifacts.
- [x] Live provider/Phoenix/benchmark claims remain `no_claim`.
- [x] Playwright screenshots are `no_claim`; no chat-demo UI behavior changed.

### Phase I. Live provider and Phoenix cadence

- [x] Generalized the OpenRouter preflight shape into deterministic provider
  preflight reports.
- [x] Defined optional OpenRouter live evidence as `no_claim` unless explicitly
  executed.
- [x] Defined optional vLLM/local smoke as local-endpoint dependent/no-claim.
- [x] Defined Phoenix expectations through validation gates and no-claim
  reasons.
- [x] Added cost/timeout/retry policy hooks through release-gate policy rows.
- [x] Recorded absent keys/endpoints as `skipped`/`no_claim`, not failures.

### Phase J. Capability pack and continuous-validation integration

- [x] Added capability-pack scenarios:
  `provider_catalog.plugin_registry.v1`,
  `provider_catalog.sanitizer_matrix.v1`,
  `provider_catalog.openrouter_preflight.v1`,
  `provider_catalog.excel_workbook_routes.v1`,
  `provider_catalog.chat_demo_research_routes.v1`.
- [x] Added deterministic evidence index for provider compatibility reports.
- [x] Wired provider report artifact types into 008 audit.
- [x] Added release-gate policy rule for provider catalog contract changes.

## Acceptance

- [x] H1 deterministic: contract tests cover plugin manifest, route plugin,
  model catalog, catalog fetch plan, capability matrix, sanitizer fixture,
  routing plan, preflight report, and compatibility report models.
- [x] H1 deterministic: registry tests cover built-ins, aliases, duplicate ids,
  replacement, and unknown/no-claim behavior.
- [x] H1 deterministic: sanitizer matrix covers OpenAI/OpenRouter/vLLM/
  DeepSeek/GLM/Gemma/Sonnet/Ollama deterministic rows.
- [x] H1 deterministic: redaction tests reject secret-shaped metadata and raw
  provider responses.
- [x] H2 replay/trace: validation artifacts include provider profile ids,
  routing plans, sanitizer verdicts, and no-claim live states.
- [x] H3 Excel AI: workbook route compatibility report exists and is referenced
  by this epic.
- [x] H3 chat-demo: deep research route compatibility report exists and is
  referenced by this epic.
- [x] H4 live provider: not required for this deterministic slice; live claims
  are `no_claim`.
- [x] H5 benchmark: not required because no quality/cost/latency movement is
  claimed.
- [x] I3 Playwright: `no_claim` because provider work did not change UI
  behavior.
- [x] I4 Artifacts: JSON/Markdown report, catalog fixture, sanitizer matrix,
  validation gates, and evidence index are saved.

## Evidence Log

- 2026-07-03: Added provider-catalog contracts in
  `agent_driver/contracts/provider_catalog.py`.
- 2026-07-03: Added deterministic registry/report/sanitizer/routing runner in
  `agent_driver/llm/provider_catalog.py`.
- 2026-07-03: Added `agent-driver provider-catalog audit`.
- 2026-07-03: Added capability-pack scenario ids and continuous-validation
  provider artifact support.
- 2026-07-03: Ran focused tests:
  `uv run python -m pytest tests/llm/test_provider_catalog.py tests/cli/test_provider_catalog_command.py tests/harness/test_capability_packs.py tests/harness/test_continuous_validation.py -q`
  -> 25 passed.
- 2026-07-03: Ran provider-catalog audit and strict capability-pack audit over
  `.agent-driver/provider-catalog/epic-012-deterministic` -> strict passed.

## Closing Note

- Status/date: completed deterministic/no-live slice on 2026-07-03.
- Changed files/PRs: local checkout changes only.
- Evidence paths: see persisted artifacts above.
- Infra gates used/skipped: deterministic tests and support-bundle artifact
  passed; live provider, Phoenix, Playwright, and benchmark gates are
  `no_claim`.
- Remaining risks: live provider behavior, Phoenix trace ids, real catalog
  freshness, and benchmark movement require explicit opt-in runs before claims.
- Rejected alternatives: no automatic provider switching and no live catalog
  fetch in deterministic runtime paths.
- Follow-up: add opt-in live provider fetch/probe commands only when a release
  policy requires live claims.
