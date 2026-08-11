# Agent coordination (C-track)

Multi-agent / subagent / coordination epics, seeded by a 2026-08-11 survey of
`agent-driver` + excel-ai against the reference runtimes in `reference/{hermes-agent,
openclaude, openhands-sdk}` and the 2025–2026 multi-agent literature (Anthropic's
multi-agent research system, LangChain Deep Agents, OpenAI Agents SDK handoffs, the
MAST failure taxonomy).

## Where we stand

We have **more low-level primitives** than most references — formal join policies
(`WAIT_ALL/ANY/K_OF_N/RACE/BEST_EFFORT`), merge modes, a per-child cost watchdog +
budget isolation, a durable subagent mailbox contract, worktree isolation,
schema-enforced tool allowlists — but they are **fragmented across two disjoint
stacks** and several are **dead-ended**:

- **(A) SDK primitives** (`sdk/subagent.py`, `async_subagent.py`, `fork.py`) —
  in-process, no persistence. The only stack excel-ai uses.
- **(B) Runtime executor** (`subagents/executor.py`, `runtime/single_agent/
  tool_stage/subagent_execution.py`) — model-planner-driven, persisted, with join/
  merge/mailbox/worktree. Rich, but **excel-ai never touches it**.

Concrete dead-ends: the mailbox parent→child continuation is written but **no running
child ever reads it** (only child→parent notification is live); the `SYNTHESIZE` merge
mode degrades to string concatenation; the SDK's own "sync group" is a **sequential
for-loop** (real concurrency only comes from the consumer); and model-driven fan-out is
**hard-capped at one level** (`subagent_origin==child` guard) with no depth budget.

The references are simpler on primitives but **more coherent**, and have things we
lack: a Markdown agent registry, live subagent steering, a deep-agent long-horizon
mode. This track closes those gaps, keeping the runtime domain-neutral.

## Epics

| ID | Epic | Gap | Proven by | Effort | Status |
| --- | --- | --- | --- | --- | --- |
| **C2** | Markdown-defined agent registry | static code roles, no hot-loadable agent types | openclaude `.claude/agents`, OpenHands `.md` | S | **DONE** |
| **C1** | Unify the two subagent stacks | SDK primitives ≠ runtime executor; join/merge unused | — (internal) | L | IN PROGRESS |
| **C3** | Mailbox fix + live subagent steering | parent→child continuation dead-ended; no mid-flight steer | openclaude `SendMessage`; MAST coordination | M | IN PROGRESS |
| **C4** | Supervisor/coordinator in the SDK | orchestrator-worker only hand-wired in excel-ai; sync group is sequential | openclaude coordinator mode, Anthropic | M | **DONE** |
| **C5** | Deep/ultra-agent mode | planner + subagents + FS + context-mgmt not composed | LangChain Deep Agents, Anthropic | L | **DONE** |
| **C6** | Handoffs / agents-as-tools | no control transfer to a peer | OpenAI Agents SDK | M | PROPOSED |
| **C7** | Governed recursion / depth budget | blunt 1-level cap, no depth budget | hermes `MAX_DEPTH` | S | PROPOSED |
| **C8** | Verifier/critic primitive | no independent validation of subagent output | MAST verification-gap | S | PROPOSED |

Guiding principle (Anthropic): multi-agent wins **only when the task decomposes into
independent parallel threads**, at ~15× the tokens — so the supervisor/orchestrator-
worker topology and honest budget/verification governance matter more than exotic
swarm topologies. Cross-cutting: design against the **MAST** failure modes
(specification ambiguity, coordination breakdown, verification gap).

---

### C2 — Markdown-defined agent registry — DONE (2026-08-11)

New `agent_driver.agents` facade: `AgentDefinition` (from Markdown + YAML
frontmatter), `parse_agent_markdown` / `load_agent_definitions`, `AgentRegistry`
(layered precedence, higher priority overrides a name clash, first-wins within a
priority), and `agent_definition_to_spec` bridging to `sdk.SubagentSpec`. Agents are
data, hot-loadable, domain-neutral. Tests: `tests/agents/test_agent_registry.py`;
example `examples/cookbook/25_agent_registry.py`. This becomes the config layer C4
(coordinator) and C5 (deep-agent) build on.

### C1 — Unify the two subagent stacks — IN PROGRESS

Collapse the SDK primitives (A) and the runtime planner-driven executor (B) so the
SDK can drive fan-out + join (`join.py`) + merge (`merge.py`) + mailbox + worktree
without a consumer re-implementing concurrency.

**Step 1 — DONE (2026-08-11):** `sdk.run_subagent_group` (`sdk/group.py`) — real
concurrent fan-out under a concurrency cap that *executes* the shared
`SubagentJoinPolicy` vocabulary (WAIT_ALL / WAIT_ANY / K_OF_N / RACE /
BEST_EFFORT_UNTIL_DEADLINE) with asyncio, returning a `SubagentGroupResult`. This
brings the runtime's join vocabulary to the SDK primitive layer and replaces the
consumer's hand-rolled `gather` + semaphore. Tests: `tests/sdk/test_subagent_group.py`.

**Step 2 — DONE (2026-08-11):** `sdk.merge_subagent_results` (deterministic
`APPEND`/`RANK`/`VOTE`/`MANUAL` over `SubagentResult`) + `sdk.synthesize_subagent_results`
(a **real** LLM `SYNTHESIZE` via one aux call, degrading to `APPEND` on error) — brings
the runtime's merge vocabulary (`SubagentMergeMode`) to the SDK and fixes the
concat-degradation. Tests: `tests/sdk/test_subagent_merge.py`; example 26 now shows
fan-out → join → merge/synthesize.

**Step 3 — DONE (2026-08-11):** retired the runtime executor's *sequential* sync-group
loop — `execute_subagent_group_sync` now runs its (parallel-budget-limited) children
concurrently via `asyncio.gather` (order-preserving, orphan-free), aligning it with the
already-concurrent background path. A fan-out of N no longer costs N× wall-clock. Test:
`tests/subagents/test_sync_child_execution.py::test_sync_group_runs_children_concurrently`.

**Step 4 — DONE (2026-08-11):** `run_subagent_group` gained per-child retry
(`retries` / `retry_on` / `retry_backoff_seconds`) — a failed child re-runs with an
exponential+jittered, abort-aware backoff taken *outside* the concurrency slot (matching
excel-ai's explorer coordinator), so the primitive now fully covers a real consumer's
hand-rolled fan-out+retry. Tests in `tests/sdk/test_subagent_group.py`.

**Remaining:** expose the subagent mailbox + worktree isolation through the SDK
surface; migrate excel-ai's `explorer_coordinator` off its bespoke `asyncio.gather` +
semaphore + retry onto `run_subagent_group` + `merge_subagent_results` (now unblocked).
Foundational for C3/C4/C5.

### C3 — Mailbox fix + live subagent steering — IN PROGRESS

**Step 1 — DONE (2026-08-11):** partial-output salvage — `SubagentResult` already keeps
its `answer` on a non-`COMPLETED` stop, but the group merge dropped it. Added
`SubagentGroupResult.partial` and an `include_partial=` option on
`merge_subagent_results` / `synthesize_subagent_results` (partial answers labeled
`(partial: <status>)`), so an orchestrator can salvage a timed-out child's work instead
of discarding it (OpenHands "partial output on non-final stop"; MAST verification-gap).
Tests: `tests/sdk/test_subagent_merge.py`, `tests/sdk/test_subagent_group.py`.

**Step 2 — DONE (2026-08-11):** live steering. `run_subagent(redirect_probe=…)` binds a
per-run probe via a per-asyncio-task `ContextVar` (`active_redirect_probe`), so the
completion loop's redirect racer steers *this* child (concurrent fan-out children stay
isolated), and `BackgroundSubagent.send(message)` course-corrects a running background
child mid-flight — its next in-flight LLM turn is re-asked with the message folded in as
a user turn (openclaude `SendMessage`). This closes the mailbox dead-end for the SDK
path without per-child runner surgery. Tests: `tests/sdk/test_subagent_steering.py`.

**Remaining:** wire the *stack-B* `subagent_mailbox` PARENT_TO_CHILD items into the same
redirect probe (so the model-planner path also steers running children), and the honest
**never-fabricate-a-pending-result** orchestrator prompt rule (a consumer-prompt concern).

### C4 — Supervisor / coordinator in the SDK — DONE (2026-08-11)

`sdk.run_coordinator` (`sdk/coordinator.py`) promotes the hand-wired
orchestrator-worker into a reusable, domain-neutral primitive: an ordered list of
`CoordinatorPhase`s, each of which builds its worker specs from the prior phases'
results (`build_specs(prior)`, sync or async), fans them out concurrently via
`run_subagent_group` under a join policy, and merges the outcome (`APPEND`/`RANK`/`VOTE`,
or a real LLM `SYNTHESIZE`). The merged string threads into the next phase's
`build_specs`, so `research → synthesize → verify` composes with no consumer glue;
agents are resolvable from the C2 registry (`agent_definition_to_spec`). A phase whose
join policy isn't satisfied halts the pipeline (`stop_on_unsatisfied`, default on) and
marks the `CoordinatorResult` `stopped_early` — the MAST coordination-breakdown guard.
Real parallelism, real synthesis, honest partial-failure semantics. Tests:
`tests/sdk/test_coordinator.py`; example `examples/cookbook/27_coordinator.py`.

### C5 — Deep / ultra-agent mode — DONE (2026-08-11)

Compose what we already have — planning (P-track todos), subagents, a filesystem
(execution-backend), and context management/compaction — into one long-horizon
"deep agent" mode, plus the **artifact pattern**: a subagent writes findings to the
shared workspace and returns a lightweight reference instead of a long lossy chat
return (Anthropic; LangChain Deep Agents' planner + subagents + filesystem + context
engineering). This is the "ultra agents" ask; highest ceiling.

**Step 1 — DONE (2026-08-11):** the artifact pattern for the SDK subagent path
(`sdk/artifacts.py`) — the deep-agent kernel. `capture_subagent_artifact` /
`capture_group_artifacts` write a child's answer to the shared workspace and return a
`SubagentArtifact` (workspace-relative path + one-line summary + char count), skipping
empty/non-`COMPLETED` children (unless `include_partial`); `artifact_references` renders
them as a compact block a downstream phase feeds the model instead of the full
concatenated findings — the ~15× token fix. `share_workspace` closes the companion gap
(an SDK child doesn't inherit `workspace_cwd` by default) by injecting a shared workspace
into each child's `app_metadata`, so a later phase can read earlier artifacts;
`SubagentSpec.with_app_metadata` backs it. Composes directly with the C4 coordinator
(capture a phase's group → thread the refs into the next phase's `build_specs`). Tests:
`tests/sdk/test_artifacts.py`; example `examples/cookbook/28_deep_agent_artifacts.py`.

**Step 2 — DONE (2026-08-11):** the `run_deep_agent` driver (`sdk/deep_agent.py`) — the
long-horizon "ultra agent" as one domain-neutral function. It decomposes a task into
independent subtasks (an LLM planner via `planner_provider`, or a supplied `planner`
callable), writes the plan to `<workspace>/plan.md`, fans out one worker per subtask via
`run_subagent_group` on a shared workspace (`share_workspace`), captures each worker's
findings as an artifact (step 1), and hands a synthesizer child the compact references —
not the concatenated findings — to produce the final answer (`include_partial` salvages
non-completed workers). Returns a `DeepAgentResult` (plan + group + artifacts + answer +
`satisfied`); an empty plan returns early, unsatisfied. Composes the whole C-track (C1
fan-out/join, C4 coordinator semantics, C5 artifacts); compaction applies for free inside
each child's run loop. Tests: `tests/sdk/test_deep_agent.py`; example
`examples/cookbook/29_deep_agent.py`.

**Remaining (folded into C7/C8, not C5):** a verify phase (C8) around the
capture→synthesize loop; optional backend-routed artifact writes (the
`WorkspaceCapableBackend` seam) rather than local pathlib; reusing the P-track
`todo_write` ledger for the plan; and driving `CompactionOrchestrator.decide` on a
per-plan-step cadence for very long horizons.

### C6 — Handoffs / agents-as-tools

Decentralized delegation: a `transfer_to_<agent>` handoff where the chosen specialist
owns the remainder of the turn, and specialist-as-tool for narrow subtasks (OpenAI
Agents SDK). Lower priority than the supervisor track — supervisor is the safer 2026
default and less MAST-prone (circular handoffs), so this is opt-in decentralization.

### C7 — Governed recursion / depth budget

Replace the blunt `subagent_origin==child` one-level guard with a configurable depth
budget + per-node independent iteration/cost budgets (hermes: `MAX_DEPTH=1` default,
per-subagent `IterationBudget=50` separate from the parent's 500). Lets deep trees
exist while preventing self-granted recursion / fork-bombs.

### C8 — Verifier / critic primitive

A first-class independent verifier that validates a subagent's output before the
parent trusts it — the MAST verification-gap fix. Composes with C4 (a verify phase)
and the eval-layer `LlmJudge` (#5).

## Recommended order

C2 (done) → C1 (done, unify) → C3 (done, fix dead-end + MAST) → C4 (done, supervisor) →
C5 (done, deep-agent) → **C7** → **C8** → **C6**.
