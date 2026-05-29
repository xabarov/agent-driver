# Multi-Agent Orchestration And Parallel Subagents

## Why This Matters

Single child agents are not enough for serious agent products. A parent agent often needs to fan out work to several specialists, compare independent attempts, race fast vs deep strategies, or gather partial evidence before synthesizing an answer.

`agent-driver` should support this without treating child agents as opaque tool calls. Parallel subagents must stay durable, cancellable, observable, budgeted, and replayable.

## Core Model

The parent run owns orchestration. Each child agent is still a normal run with its own checkpoints, events, model profile, tools, context, and terminal state.

Add two orchestration concepts:

- `SubagentRun`: one child run spawned by a parent.
- `SubagentGroup`: a fan-out/fan-in group created by one parent step.

Minimum `SubagentGroup` fields:

- `group_id`;
- `parent_run_id`;
- `parent_checkpoint_id`;
- `parent_step_id`;
- `purpose`;
- `join_policy`;
- `max_parallel`;
- `deadline`;
- `budget`;
- `child_run_ids`;
- `terminal_state`;
- `merge_provenance`.

This lets the parent checkpoint a group before children start, resume after crash, collect late children, and avoid duplicate spawn on retry.

## Spawn Contract

Child spawn should be idempotent:

- deterministic `idempotency_key` per child task;
- parent checkpoint saved before scheduling children;
- child row inserted before execution starts;
- retrying the parent step reuses existing child rows instead of spawning duplicates;
- side-effecting child tools inherit or narrow parent policy, never widen it.

The spawn API should support:

- one child task;
- a list of child tasks as one group;
- child-specific agent profile, tool manifest, context attachments, and deadline;
- shared group budget across all children.

## Join Policies

The parent needs explicit join behavior instead of ad hoc polling:

- `wait_all`: all children must finish or the group fails/partially fails.
- `wait_any`: first successful child can complete the group.
- `k_of_n`: group succeeds after `k` successful children.
- `best_effort_until_deadline`: collect all completed children until deadline, then synthesize partial results.
- `race`: first successful child wins and remaining children are cancelled.
- `manual_review`: parent pauses and asks a human to choose/merge child outputs.

Every join result should record:

- completed child ids;
- failed/timed-out/cancelled child ids;
- whether the result is complete or partial;
- merge strategy;
- evidence/output pointers used by the parent.

## Merge Contract

Merging is part of runtime semantics, not just prompt text.

Supported merge modes:

- `append`: keep separate child outputs with provenance.
- `rank`: score child outputs and select one or more winners.
- `synthesize`: call a parent/model step with bounded child summaries.
- `vote`: require agreement or majority over structured fields.
- `manual`: produce an interrupt with child summaries for human choice.

The merge record should preserve:

- source child ids;
- output/artifact pointers;
- summary text shown to the parent;
- conflict notes;
- discarded outputs and reason;
- final selected/synthesized output pointer.

## Budgets And Backpressure

Parallelism needs strict limits:

- `max_parallel` per group;
- max child runs per parent run;
- per-child and group deadlines;
- token/cost budgets per child and group;
- queue capacity and worker lease limits;
- cancellation on parent cancellation;
- policy for late child completion after parent already moved on.

Budget exhaustion should produce a typed terminal state, not an ambiguous failure.

## Context Isolation

Each child should receive only the context it needs:

- child task;
- scoped attachments/artifacts;
- allowed tools and policy;
- relevant parent summary;
- no broad inherited scratchpad unless explicitly requested.

Child output returns to the parent as bounded summaries plus artifact pointers. The parent should not ingest full child transcripts by default.

## Events

Add typed events for orchestration:

- `subagent_group_started`;
- `subagent_spawned`;
- `subagent_group_join_waiting`;
- `subagent_group_joined`;
- `subagent_group_cancelled`;
- `subagent_merge_started`;
- `subagent_merge_completed`;
- `subagent_group_failed`.

These events should include `group_id`, `parent_run_id`, child run ids, checkpoint ids, join policy, and budget state.

## Execution Modes

Start simple:

- in-process sync child graph for deterministic tests;
- local background executor for concurrent local runs;
- later external queue/worker adapter for real parallelism.

The public contract should not depend on the execution mode. A `wait_all` group should behave the same whether children run in-process, background threads/processes, or a queue.

## Current Runtime Shape

The implemented local path now has three layers:

- `AgentProfile.COORDINATOR` plus a coordinator prompt snapshot for deliberate
  worker delegation.
- Built-in worker definitions: `worker`, `researcher`, `implementer`,
  `verifier`.
- Durable subagent groups/runs with sync and `asyncio_background` execution.

Worker roles narrow child tool policy rather than widening parent policy.
For example, a `researcher` can receive web/read/search tools while a
`verifier` stays on read/search/python verification tools. Parent deny lists
still win.

Child context handoff is intentionally bounded:

- parent summary is truncated;
- artifact/digest refs are capped;
- worker handoff rules are included;
- scratchpad defaults to bounded private state;
- artifact handoff defaults to refs-only.

Child outputs return as:

- a bounded summary used for merge;
- `child_artifact_refs` capped to a small list;
- `output_pointer` to the first child artifact when present;
- merge provenance marking whether artifact refs were carried.

## Steering And Mailbox

Parent-to-child and child-to-parent communication uses durable control and
mailbox stores:

- `send_message_tool` appends continuation messages to an existing child row.
- `task_stop_tool` marks an existing child as cancelled.
- Background child completion queues a `later` parent notification and mirrors
  it into the subagent mailbox.
- Status collection APIs expose groups, runs, and pending mailbox items without
  forcing the parent to block on every child.

The parent should continue an existing child when context is still valuable
instead of spawning a duplicate worker for the same task.

## Workspace Isolation

Child runs inherit parent `workspace_cwd` by default. A task may provide
`metadata.cwd` or `metadata.workspace_cwd`; the override is accepted only if it
resolves inside the parent workspace.

Write-heavy children may request:

```json
{"metadata": {"isolation_mode": "worktree"}}
```

When the parent workspace is a git repository, the runtime creates a detached
git worktree for the child, passes that worktree as `workspace_cwd`, and removes
it when the child reaches a terminal state. This keeps child writes out of the
parent workspace while preserving normal filesystem and shell tool semantics.

Process/tmux/remote backends remain future adapters. The current local path is
`asyncio_background` plus mailbox, abort propagation, cwd/worktree isolation,
and bounded artifact refs.

## Evaluation Cases

Add deterministic eval cases before relying on LLM-as-judge:

- fan out three children and `wait_all`;
- one child times out under `best_effort_until_deadline`;
- `race` cancels remaining children after first success;
- parent crash after spawn resumes without duplicate child rows;
- cancelled parent cancels pending/running children;
- merge preserves provenance and marks partial outputs;
- budget exhaustion stops scheduling new children.

## Roadmap Implication

Phase 9 should be renamed from "Subagents" to "Subagents And Parallel Orchestration". The first cut can implement sync child runs and group metadata; true worker-backed parallelism can remain behind a local/background executor and later queue adapters.
