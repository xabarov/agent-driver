# Compaction — research log

The epic's first phase. Goal: understand our current design, learn from prior art
(reference projects + external), and converge on a recommended direction before
writing code. Sections are filled as research proceeds.

## 1. Our current architecture (as-is)

Entry: `runtime/single_agent/context_management/compaction_stage.py::apply_compaction_if_eligible`
runs before final provider completion. Key modules:

- `context/compaction/eligibility.py` — when to compact.
- `context/compaction/orchestrator.py` — decide mode + attempt lifecycle + circuit breaker.
- `context/compaction/llm_full.py` — full LLM summary of a history excerpt.
- `context/compaction/session_memory_*.py` — session-memory extraction/store.
- `context/compaction/partial.py`, `span_collapse.py`, `tool_history.py`,
  `tool_clear.py`, `microcompaction.py` — partial strategies.
- `context/compaction/post_compact.py` — post-compaction cleanup/reinjection.
- `context/run_budget.py` + `contracts/context/run_budget.py` — the resolved
  context budget (`effective_context_budget`, `max_chars`, `max_compaction_chars`,
  `context_window_estimate`).

_(Filled by the architecture-map research task.)_

## 2. Tunables & magic-number inventory

_(Every knob: name, default, where it lives, what it SHOULD derive from. Seeds in
`BUGS.md` — expand to the full list here.)_

## 3. Reference projects

`reference/hermes-agent` and `reference/openclaude` — how they handle context
compaction / summarization / memory, and what (if anything) is worth adopting.

_(Filled by the reference-survey research task.)_

## 4. External prior art (surveyed 2026-08)

**Anthropic (Claude platform) — closest analogue to our design.**
- *Context editing* (`clear_tool_uses_20250919`): server-side clearing of old
  tool-use/result pairs + thinking blocks once context passes a threshold —
  described as the "safest, lightest-touch" compaction. Runs before token
  counting and after prompt-cache lookup. We have analogues (`tool_clear`,
  `tool_history`) but run them client-side.
- *Compaction*: server-side full-conversation summary near the window limit —
  analogous to our `llm_full`.
- *Memory tool*: the agent writes notes persisted OUTSIDE the window and pulls
  them back later — an agentic version of our `session_memory`.
- Reported gains: context-editing alone +29%, +memory +39% over baseline; a
  100-turn web-search eval cut tokens 84% and completed runs that otherwise
  exhausted context. Anthropic recommends server-side compaction over SDK-side.
- "Protect more context" (Claude Code): protecting recent/important context
  improved quality — validates our protected-turn policy; suggests leaning into
  protection + cheap tool-result clearing *before* full summarization.

**LangGraph / LangMem.** Running-summary node fired conditionally on a token/
message threshold, replacing old messages with a rolling summary
(`SummarizationNode` / `summarize_messages`); `trim_messages` for hard trims.
LangMem extracts facts/behaviours to a long-term store. Framing: "the context
window is the only reality — memory's job is to decide what goes in, when, and in
what form."

**MemGPT / Letta.** Tiered memory (in-context vs external) with self-editing and
paging between tiers.

**Deriving the model window (directly addresses BUG-2).** LiteLLM exposes
`get_max_tokens()` and a model catalog (`context_window` / `max_input_tokens` /
`max_output_tokens`). The window can be *derived from a model registry / provider*
rather than a static 12K estimate.

**Adoptable ideas (ranked):**
1. **Derive the model window from the provider/registry** (LiteLLM-style), config
   override next, a *modern* floor last — fixes BUG-2 at the root instead of a
   blind 12K default.
2. **Express caps as fractions of the resolved window**, not absolute char magic
   numbers — fixes BUG-1/BUG-3 (`_MAX_SCALED_COMPACTION_CHARS`, base caps).
3. **Cheap tool-result clearing as an explicit FIRST tier** before LLM summary
   (Anthropic's "lightest touch"); we have the pieces — make it the default first
   tier and measure token/quality impact.
4. **Agentic memory (write-notes-outside-window)** as a complement to
   `session_memory` for material that must survive summarization.
5. **Incremental/rolling summary** (update a running summary) instead of
   re-summarising the full excerpt each time — cheaper on repeated compaction.

Sources: Anthropic [context management](https://claude.com/blog/context-management),
[context editing docs](https://platform.claude.com/docs/en/build-with-claude/context-editing),
[memory tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool),
[effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents);
[LangMem summarization](https://langchain-ai.github.io/langmem/guides/summarization/);
[lang-memgpt](https://github.com/langchain-ai/lang-memgpt);
[LiteLLM token usage](https://docs.litellm.ai/docs/completion/token_usage),
[LiteLLM model catalog](https://github.com/BerriAI/litellm/discussions/21029).

## 5. Design options & recommendation

_(After 1–4: 2–4 candidate directions with trade-offs, then a recommended one and
the contract/knob/behaviour/SemVer delta it implies.)_
