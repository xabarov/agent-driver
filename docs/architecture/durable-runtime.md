# Durable Runtime, Checkpointing, And Worker Execution

## Why This Matters

Modern agent runtimes are not just chat loops. LangGraph's production argument is that agents are slow, flaky, long-running, and non-deterministic. This pushes six runtime capabilities into the foundation: parallelization, streaming, task queues, checkpointing, human-in-the-loop, and tracing.

`agent-driver` currently has good contracts for run output, tool traces, subagents, and compaction, but the first analysis under-specifies durable execution. This should become a core subsystem before complex subagents and LLM compaction are extracted.

External references:

- [Building LangGraph: Designing an Agent Runtime from First Principles](https://blog.langchain.com/building-langgraph)
- [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview)
- [LangGraph durable execution](https://docs.langchain.com/oss/python/langgraph/durable-execution)

## Required Capabilities

### Checkpoint Protocol

The engine should define a storage-neutral checkpoint interface:

- save checkpoint after successful graph step;
- load latest checkpoint for a thread/run;
- load a specific checkpoint by `checkpoint_id`;
- list checkpoints for time-travel debugging;
- fork from a checkpoint into a new run branch;
- attach checkpoint metadata: graph id, node id, channel versions, run id, tenant/user metadata, created time.

Current implementation status (after Phase 2 first cut):

- protocols exist in `agent_driver/runtime/storage/protocols.py` (`CheckpointStore`, `RuntimeEventLog`; import via `agent_driver.runtime.storage`);
- in-memory backend exists for tests and fast local runs;
- SQLite backend exists for local durability and replay tests;
- runner persists checkpoint after successful steps and can resume from checkpoint id.

Gaps to close before production-grade multi-DB support:

- branch/fork semantics are still pending in adapters (capability is currently `false`);
- no migration/versioning contract for persistent stores yet.

Initial backends:

- in-memory for tests;
- SQLite for local/single-node usage;
- Postgres for multi-worker production.

### DB Strategy For Checkpoints (What To Add Next)

Recommended backend priority for `agent-driver`:

1. SQLite (already implemented) for local dev, demos, single-process deployment.
2. Postgres (next logical production backend) for multi-worker/API deployments.
3. Optional adapters later (for specific constraints): MySQL/MariaDB, Redis, document DB.

Why Postgres is the most logical next step:

- strong transactional semantics and indexing for run/checkpoint/event timelines;
- mature JSON/JSONB support for metadata and evolving payloads;
- reliable concurrency controls for worker leases and resume flows;
- ubiquitous managed offerings and tooling for migrations/backups/observability.

When to consider others:

- MySQL/MariaDB: if target infra is already standardized there and features needed are basic.
- Redis: good for ephemeral queue/lease state, but weak as primary durable checkpoint history.
- MongoDB/document DB: acceptable for JSON-heavy payloads, but replay ordering and strict transactional flow should be carefully validated.

### Compatibility Contract For Future Backends

To avoid forgetting multi-DB support, each new backend should satisfy a shared contract:

- atomic `save` semantics per step;
- stable ordering for `latest` and event `seq`;
- idempotent writes for retry paths;
- explicit parent-chain behavior (`parent_checkpoint_id`);
- backend migration version field in persisted payload;
- deterministic replay tests shared across all backends.

Minimal backend conformance test matrix:

- save/load/latest parity with in-memory reference backend;
- resume after simulated failure;
- concurrent write safety (at least smoke-level worker contention test);
- replay consistency for stored events/checkpoints;
- retention/cleanup behavior does not break resume invariants.

Current protocol notes (implemented):

- `list_checkpoints(run_id, limit)` is the production-facing listing API;
- `snapshot_debug()` is debug/test-oriented and should not be used by app runtime paths;
- `capabilities()` exposes backend traits (`transactional_writes`, `supports_retention`, etc.) for ops/test decisions.

Integration UX notes (implemented):

- `RuntimeStoreFactoryConfig` + `create_runtime_store_bundle(...)` provide one entrypoint for app integration;
- `runtime_store_config_from_env()` standardizes env-driven backend selection;
- `preflight_runtime_store(...)` provides lightweight readiness checks before runner init.

### PostgreSQL Adapter Notes (Phase 2.5)

`PostgresRuntimeStore` is implemented as an optional extra:

- install via `pip install -e .[postgres]`;
- DSN-driven config (`AGENT_DRIVER_POSTGRES_DSN` for live checks);
- schema bootstrap helper for dev/test (`ensure_schema`);
- JSONB payload with indexed run/sequence timeline columns.

Migration expectations:

- current implementation includes bootstrap DDL and `SCHEMA_VERSION` constant;
- both SQLite and Postgres stores now persist `runtime_schema_version` metadata in backend schema tables;
- production apps should run explicit SQL migrations in their deployment pipeline;
- engine-level adapters should stay lightweight and avoid hard-coupling to a specific migration framework.

Backend selection matrix:

- `memory`: unit tests, ephemeral local runs, no durability.
- `sqlite`: local/single-node durable runs, simple operational model.
- `postgres`: multi-worker/shared API deployments, stronger transactional semantics.

Redis note:

- Redis should be treated as queue/lease infrastructure or cache;
- do not use Redis as primary source of durable checkpoint history unless a separate durability/replay design is introduced.

### Atomic Step Model

Every graph node should be treated as an atomic step:

- inputs are read from checkpointed state;
- side effects are either idempotent or guarded by idempotency keys;
- successful node output is checkpointed;
- failure leaves the prior checkpoint resumable.

Tool calls with external side effects need stricter treatment than pure LLM calls.

### Resume And Replay

The runtime should support:

- resume interrupted run;
- retry failed node from last checkpoint;
- replay run from initial state for eval/debug;
- replay from checkpoint with modified model/tool settings;
- branch from checkpoint for counterfactual evaluation.

This is the foundation for durable approvals, eval replay, and production incident analysis.

### Idempotency And Side Effects

The engine should distinguish:

- pure nodes: no external side effect;
- read-only tool nodes;
- reversible write nodes;
- irreversible write nodes.

For side-effecting tools, require:

- idempotency key;
- approval policy;
- side-effect receipt in trace;
- dedupe on retry;
- clear terminal state if retry is unsafe.

## Worker And Queue Runtime

`agent-driver` should not require a queue for local use, but the architecture should not assume request-bound execution.

Minimum run queue concepts:

- `RunRecord`: queued/running/paused/completed/failed/cancelled;
- lease owner and heartbeat;
- run priority;
- retry count;
- deadline and cancellation token;
- trace id;
- checkpoint pointer.

Execution modes:

- direct in-process call for tests and simple apps;
- background local worker for development;
- external queue adapter later: Redis, Postgres, Celery, Temporal, or custom.

## Streaming With Durability

Streaming is not only token streaming. Long agent runs need step updates:

- node started/completed;
- tool call started/completed;
- checkpoint saved;
- interrupt requested;
- subagent started/completed;
- progress event;
- terminal event.

The event stream should be reconstructable from persisted run events, so a client can reconnect after page reload or network loss.

## Design Implications For `agent-driver`

Add package areas:

```text
agent_driver/
  checkpointing/
    protocol.py
    memory.py
    sqlite.py
    postgres.py
  execution/
    runner.py
    queue.py
    worker.py
    leases.py
    cancellation.py
  runtime/
    replay.py
    branching.py
```

Add contract fields:

- `run_id`;
- `attempt_id`;
- `checkpoint_id`;
- `parent_checkpoint_id`;
- `branch_id`;
- `resume_token`;
- `idempotency_key`;
- `terminal_reason`;
- `cancellation_reason`.

## MVP Recommendation

Do not wait until after subagents to implement checkpoints. The MVP runtime should include at least:

- in-memory checkpoint backend;
- SQLite checkpoint backend;
- run id and checkpoint id in every event;
- resume from last checkpoint;
- cancellation token;
- fake replay tests.

Postgres and distributed workers can come later, but the contracts must exist from the start.
