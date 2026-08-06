# Design Decision — Option B1: Condenser protocol + pipeline

Status: proposed (awaiting sign-off). Branch: `epic/compaction-optionB`. Parent:
[`DESIGN-optionB-condenser-pipeline.md`](DESIGN-optionB-condenser-pipeline.md).

Scope: refactor the monolithic compaction mode-dispatch into a **cost-ordered pipeline of
composable condensers**, on top of the existing (already-non-destructive) protocol_messages
→ request.messages derivation. No durability/checkpoint change (that is B3). No amortized
summary yet (that is B2, gated on measurement).

## Current shape (to unify)

The modes/primitives have heterogeneous signatures but one common shape —
`messages (+ params) → reduced messages + audit`:
- `tool_clear` → `ToolClearResult`; `tool_history(messages,…) → (list[ChatMessage], audit)`;
  `span_collapse` → `list[ChatMessage]`; `partial → PartialCompactionOutput`;
  `session_memory` / `llm_full` rewrite `request.messages` inside `compaction_stage`.
- Dispatch today: `orchestrator.decide` picks ONE `CompactionMode`
  (`session_memory → llm_full → partial`), fallthrough on failure. `span_collapse` and
  `tool_clear.idle_gap` are implemented but **unwired**.

## The protocol

```python
@dataclass(frozen=True, slots=True)
class CondenseResult:
    messages: list[ChatMessage]      # the (possibly) reduced messages
    changed: bool                    # did this condenser alter anything
    chars_freed: int                 # chars removed vs input (honest; 0 if no-op)
    audit: dict[str, Any]            # redaction-safe receipt for the run audit
    exhausted: bool = False          # tried but could not free meaningful space

class Condenser(Protocol):
    name: str
    def applies(self, ctx: CondenseContext) -> bool: ...      # cheap gate
    async def condense(
        self, messages: list[ChatMessage], *, ctx: CondenseContext
    ) -> CondenseResult: ...
```

`CondenseContext` carries the resolved budget (window, `max_compaction_chars`,
`chars_per_token`), protection predicate (`_is_protected_message`), orchestrator handles,
and host config — everything the modes read today from `host`/`context`.

A `CondenserPipeline` runs its condensers in order; after each it re-checks the budget and
**stops as soon as the request fits** (`used ≤ target`), so a cheap tier can make the
expensive summary a no-op. `minimum_progress` (default from BUG-3-style config, ~0.1)
rejects a condenser whose `chars_freed` is below the floor (anti-thrash), mirroring
OpenHands. `exhausted=True` from the last tier yields an honest "could not fit" outcome
(feeds the circuit breaker) — never a false `success` (fixes **BUG-7**).

## Pipeline order (cost-ascending, cheapest first)

1. `microcompaction` / `tool_arg_truncation` — deterministic, no-LLM (already pre-passes).
2. `tool_clear` — clear old tool-result *content* (wire the dead idle-gap primitive).
3. `tool_history` — tiered old-tool-result shrink.
4. `span_collapse` — collapse the oldest whole-turn span (wire the dead primitive; selection
   only today — give it the aux summariser via the pipeline).
5. `partial` — keep-tail prefix summary; made token-aware + honest (BUG-7).
6. `session_memory` — deterministic memory block when fresh.
7. `llm_full` — full aux-LLM summary (last resort).

This is the Anthropic/openclaude/hermes "cheap tiers first, LLM last" model; today only
6→7→(5) run, and only one at a time.

## Mapping the current modes

Each existing mode/primitive becomes a `Condenser` wrapping its current body (behaviour
preserved). `orchestrator.decide` becomes pipeline *configuration* (which condensers are
enabled + order) rather than a single-mode selector; the circuit breaker + attempt lifecycle
stay, now scoped to the pipeline run.

## Phasing within B1 (each test-gated)

- **B1a — protocol + pipeline FOUNDATION (DONE, 2026-08-06).** `Condenser` /
  `CondenseContext` / `CondenseResult` / `CondenserPipeline` in
  `context/compaction/condenser.py`, with unit tests
  (`tests/context/test_condenser_pipeline.py`). Purely additive — **not yet wired into
  the live dispatch**. Nuance discovered while implementing: the current
  `apply_compaction_if_eligible` dispatch is a **mode-decision tree** (`orchestrator.decide`
  picks one mode + specific `attempted_llm_full` fallthroughs), NOT a clean pipeline —
  so re-encoding it behaviour-neutrally would just reproduce the tree with no gain. The
  clean cost-ordered pipeline IS the behaviour change; hence the cutover moves to B1b.
- **B1b — wire the pipeline in: cost-ordering + dead primitives + honest partial.** Port
  each mode/primitive to a `Condenser`, replace the dispatch tree with the pipeline (cheap
  tiers first), wire `tool_clear`/`span_collapse`, make `partial` token-aware + honest
  (BUG-7), enable `minimum_progress`. Behaviour change behind flags; measured with the eval
  runner (recall/hallucination/provenance/budget_efficiency + token/cost delta).

## Contract / SemVer

Internal `Condenser`/`CondenserPipeline` in `context/compaction/` (not a public wire
contract). New documented compaction knobs: pipeline order, per-condenser enable,
`minimum_progress`. **Minor**; B1a behaviour-neutral, B1b flagged; BUG-7 is a correctness fix.

## Open decisions for sign-off

1. **Protocol shape** — `CondenseResult` fields + the `applies`/`condense` split as above?
   (Recommend as written; `applies` keeps the cheap gate out of `condense`.)
2. **Do B1a first** (behaviour-neutral pipeline refactor), land + merge, THEN B1b
   (cost-order + primitives + BUG-7)? **Recommend yes** — smallest safe steps.
3. **`llm_full` vs `session_memory` order** at the end — keep session_memory before llm_full
   (current) or after? (Recommend keep current in B1a; revisit in B1b with the freshness-gate
   redesign.)
