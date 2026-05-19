# Next Stage Follow-up Tracks

This file tracks the follow-up streams that should remain separate from the
Phase 10 SDK/streaming baseline PRs.

## Follow-up Queue

| Track | Scope | Status | Why separate |
| --- | --- | --- | --- |
| Phase 8 quality hardening | replay assertions for context-quality fixture + live OpenRouter recall lane | DONE | Compaction quality gates should not be coupled with SDK/adapters API changes |
| Phase 9 crash safety | stale-running reconciliation, idempotent retry reuse metadata, bounded merge provenance | OPEN | Subagent lifecycle correctness is a separate risk domain from stream contracts |
| Runtime storage layout | consolidate flat `sqlite_store.py` / `postgres_store.py` into `runtime/storage/` | OPEN | Persistence path moves can create import and migration noise in unrelated PRs |
| Ollama live lane | keep optional local-service lane with endpoint preflight skip | OPEN (optional) | CI and developer machines may not run local Ollama; OpenRouter live lane remains required |

## Completed in Phase 10 hardening

- Stream contract metadata/version fields + lifecycle projection coverage;
- runtime streaming helper extraction and deterministic token ordering;
- typed SDK config + `run_text` and resume shortcuts;
- SSE reconnect backfill and CLI replay/tail/tree baseline handlers;
- docs/examples refresh for app-facing SDK + streaming usage.
- deterministic context-quality fixture + retention scoring gates;
- replay assertions for planning/token-pressure/trim/microcompaction visibility;
- opt-in OpenRouter live lane with strict JSON recall assertions;
- strategy comparison baseline report with recall/provenance/budget metrics.

## Current Validation Baseline

- offline suite: green (`AGENT_DRIVER_RUN_LIVE_TESTS=0`);
- OpenRouter live lane: green (`-m live -k "not ollama"`);
- Postgres live lane: green (`tests/runtime/test_postgres_store_live.py`);
- Ollama lane: optional and skip-on-unavailable endpoint.
