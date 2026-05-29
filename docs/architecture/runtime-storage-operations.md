# Runtime Storage Operations Guide

This note captures non-CLI operational guidance for runtime checkpoint/event stores.

## Backend Selection Matrix

- `memory`
  - Best for: tests and ephemeral local runs.
  - Durability: none.
  - Operational overhead: none.
- `sqlite`
  - Best for: single-node local/dev services.
  - Durability: file-based.
  - Operational overhead: low.
- `postgres`
  - Best for: multi-worker and shared API deployments.
  - Durability: production-grade with transactional semantics.
  - Operational overhead: medium/high (migrations, retention, indexing, pooling).

## Postgres Migrations

Runtime Postgres storage now uses ordered schema migrations (`v1`, `v2`) in
`agent_driver/runtime/storage/postgres_sql.py`.

- `v1`: bootstrap tables and baseline run/seq indexes.
- `v2`: retention-friendly `created_at` indexes and unique `(run_id, seq)` index.

Recommended deployment flow:

1. Apply SQL migrations in app pipeline before rollout.
2. Keep `auto_create_schema=True` for local/dev only.
3. For production, prefer `auto_create_schema=False` and explicit migration jobs.

## Retention and Indexing Guidance

Runtime retention must preserve resume/replay invariants:

- Keep checkpoints/events for active runs.
- Prune only by age windows that exceed expected recovery horizon.
- Prune events and checkpoints together (or with a safe lag), not independently.
- Keep `(run_id, seq)` uniqueness invariant for deterministic event replay.

The Postgres store exposes helper methods:

- `prune_events_before(before=...)`
- `prune_checkpoints_before(before=...)`

These methods are intentionally low-level; scheduling policy is left to app operators.

## Connection and Isolation Guidance

Factory/env support now includes:

- `AGENT_DRIVER_POSTGRES_CONNECT_TIMEOUT_SECONDS`
- `AGENT_DRIVER_POSTGRES_APPLICATION_NAME`

Defaults are conservative and aimed at backend service stability:

- short connect timeout for fast failover behavior;
- explicit application name for connection/pool observability.

Transaction model:

- writes (`save`, `append`, prune helpers) use explicit transactions;
- reads use autocommit for low-latency snapshots;
- ordering remains deterministic via `created_at` + `seq` constraints.
