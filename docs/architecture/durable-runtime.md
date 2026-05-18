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

Initial backends:

- in-memory for tests;
- SQLite for local/single-node usage;
- Postgres for multi-worker production.

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
