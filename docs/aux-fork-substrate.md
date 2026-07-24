# Cache-safe aux-call substrate (epic 034)

Status: **v1** (epic 034). Consolidates the engine's "side" LLM work — compaction
summaries, memory fact extraction, structured emits, follow-up suggestions,
session titles — onto one substrate with three guarantees the ad-hoc calls
lacked: honest usage accounting, optional parent-cache reuse, and a raw-free
fork event.

Reference anchors: openclaude `src/utils/forkedAgent.ts` (cache-safe params +
usage + isolation + fork event), hermes `tools/async_delegation.py` (background
idle-turn delivery), `tools/memory_tool.py` (frozen-snapshot), `agent/auxiliary_client.py`
(single aux resolver + separate accounting), `agent/title_generator.py` (fire-and-forget consumer).

---

## 1. The substrate — `agent_driver/llm/aux.py`

`aux_completion(*, provider, messages, model=None, task="aux", cache_prefix=None,
tools=None, tool_choice=None, temperature=0.0, reasoning=None, max_tokens=None,
metadata=None, cost_ledger=None) -> LlmResponse`

One cache-safe side-call. Guarantees (mirroring openclaude's four):

1. **Cache-safe.** With a `cache_prefix` (`AuxCachePrefix(messages, enable_prompt_cache)`)
   the parent's message prefix is prepended verbatim and `enable_prompt_cache` is
   turned on, so the call rides the parent's prompt cache. Without it, the call is a
   cheap independent request (`enable_prompt_cache=False`).
2. **Usage tracked.** The response usage merges into `cost_ledger` tagged `aux_task=<task>`
   (`merge_aux_usage`), so aux spend lands in the run receipt — previously only
   compaction merged; memory extraction / structured emits were lost.
3. **Isolated.** A plain `provider.complete`; it mutates no parent state.
4. **Observable.** `aux_fork_event_payload(response, task=...)` yields a raw-free
   marker (input/output/cache tokens + derived hit-rate) for an observability event
   (epic 037) — counts only, never text.

### The hard cache rule (openclaude, do not violate)

A cache-sharing fork MUST NOT override `model`, `tools`, or thinking/`reasoning`
vs the parent — those are part of the provider cache key, and changing them busts
it (openclaude PR #18143 set `effort:'low'` on a fork → 45× cache-write spike,
92.7%→61% hit rate). Deny tools via policy, **never by shrinking the tools array**
(an empty array is a different cache key → 0% hit). The only safe fork-only knobs
are the abort handle, transcript-skip, and the cache-write marker position.

## 2. Migrated consumers (phase C)

- **`structured_completion`** (`llm/structured.py`) — gained `cost_ledger` + `task`;
  every attempt's usage merges when a ledger is passed. Memory fact extraction and
  any future synchronous structured caller are now accountable.
- **Context compaction** (`context_management/compaction_stage.py`) — the compaction
  model now resolves through the per-task registry `aux_model_for("compaction")`
  (→ `auxiliary_model` → `compaction_model`) instead of reading `auxiliary_model`
  directly, so it shares the one 032 aux-backend seam. Its usage merge (via
  `_account_compaction_cost`) is unchanged.

Deferred with reason: **memory fact extraction** runs off the critical path in a
background `defer_sync` task AFTER the terminal event + receipt are emitted
(`MemoryLifecycleHook.on_run_completed`), so threading the run ledger into it has
no receipt value — the run is already closed. The `cost_ledger` seam exists for
synchronous callers; the background extractor keeps its own provider.

## 3. Background completion delivery (phase B — contract)

For a fork whose result must RE-ENTER a live conversation (not applicable to
single-shot chat, but part of the contract), the hard invariant is hermes':
**deliver the completion as a NEW idle turn, never spliced between a `tool_result`
and an assistant message** — that keeps strict role alternation legal and the
prompt cache intact ("never mutate past context"). The engine already provides the
two mechanisms this contract needs:

- **Background scheduling** — `MemoryLifecycleHook`'s `defer_sync` pattern:
  `asyncio.create_task(...)` tracked in a set with a reap callback, drained bounded
  at `shutdown()` (30s). Any fork schedules the same way.
- **Idle-turn re-entry** — the subagent executor's background mode
  (`execute_subagent_group_background`) returns immediately and merges results back
  through the bounded parent-write path, never mid-turn.

A completion payload must be **self-contained** (goal + context + result), because
by the time it surfaces the parent may be deep in unrelated context (hermes
`async_delegation` payload block). A durable completion queue with crash-recovery
re-delivery (hermes `restore_undelivered_completions`) is **out of scope** here —
low ROI for the single-shot chat host; documented so a future agentic host adds it
deliberately rather than reinventing the delivery timing.

## 4. Frozen-snapshot discipline (where the engine injects memory into the prompt)

Per hermes `memory_tool.py`: memory injected into the system prompt must be a
**frozen snapshot captured once at run start** — mid-session writes go durable to
disk/store but the prompt is NOT mutated until the next start, so the prefix cache
lives the whole session. The engine's recall block is rendered per-run from the
store at start (`MemoryLifecycleHook.on_run_start`), not mutated mid-run — the
snapshot discipline holds. A cache-sharing fork must read the same snapshot, never
a live re-render.

## 5. Acceptance (phase D)

- A fork passing `AuxCachePrefix` builds a request with the parent prefix +
  `enable_prompt_cache` (rides parent cache); without it, cache stays off
  (`tests/llm/test_aux_substrate.py`).
- Usage merges into the ledger tagged by task; no-op on missing ledger/usage/model.
- The fork event payload is raw-free (counts + hit-rate, no text).
- MeetScript chat sweep: no latency/answer regression (aux substrate is additive;
  compaction/structured paths behave identically, only accounting + model
  resolution changed).

## Not in scope

Full agentic fork (isolated ToolUseContext, sidechain transcript) — that already
exists for subagents (`subagents/cache_safe_params.py`, `sdk/fork.py::fork_subagent`);
this substrate is the LIGHTWEIGHT single-call path for side work. Durable
completion queue (§3). Cross-process cache-prefix sharing (MeetScript suggestions
run host-side with their own provider).
