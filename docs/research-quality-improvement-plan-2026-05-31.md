# Research Quality Improvement Plan

Дата: 2026-05-31.

Цель: поднять качество research-ответов agent-driver до уровня, где агент не
останавливается после первичного `web_search`, а выполняет достаточный цикл
`search -> fetch/read -> synthesize -> cite -> final`, сохраняя нашу общую
парадигму Python Zen: сначала простые prompt/runtime guards и trace criteria,
а не тяжелый DAG.

Архитектурное расширение по provider/runtime качеству зафиксировано отдельно:
[research-provider-quality-architecture-plan-2026-05-31.md](research-provider-quality-architecture-plan-2026-05-31.md).
Оно покрывает provider capability profiles, bounded repair turns,
unknown-tool guardrails, provider failure UX и Phoenix-backed live gates.

## Problem Statement

Сравнение `docs/test-examples/fork-join-queues/gpt-5.5` показало:

- прямой OpenRouter-ответ дошел до проверенных источников, выделил ключевые
  работы и дал практический алгоритм расчета fork-join модели;
- наш ответ остановился на первичном поиске и честно сообщил, что не смог
  открыть/проверить страницы;
- итоговая проблема не в модели, а в research loop: `web_search` засчитался
  как прогресс, но `web_fetch`/source verification не стали обязательным
  следующим шагом для research-report задач.

## External Best Practices

- OpenAI web-search guidance: когда модель использует web search, ответ может
  опираться на результаты поиска и отдавать citations/links; tool surface
  должен быть явным для конкретного request
  ([OpenAI Web search](https://platform.openai.com/docs/guides/tools-web-search?api-mode=responses)).
- OpenAI function/tool calling: модель выбирает только из tools, переданных в
  текущем request; это поддерживает наш dynamic prompt assembly принцип
  ([OpenAI Function calling](https://developers.openai.com/api/docs/guides/function-calling)).
- Anthropic tool-use guidance: tool descriptions должны объяснять, когда tool
  использовать, параметры и ограничения; точность растет, когда tool surface и
  инструкции узкие
  ([Anthropic Define tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)).
- Anthropic про web search / research: web search подходит для свежих и
  источниковых вопросов; extended research нужен для глубокой многошаговой
  работы с несколькими источниками
  ([Claude support: web search, extended thinking, research](https://support.claude.com/en/articles/11095361-when-should-i-use-web-search-extended-thinking-and-research)).

## Neighbor Project Findings

### Hermes

Useful patterns from `/home/roman/pyprojects/ML/hermes-agent`:

- `agent/prompt_builder.py` has strong tool-use enforcement:
  every response either makes tool progress or delivers a final result; no
  promises like "I will search/write" without doing it.
- The same prompt block has prerequisite checks: do discovery/lookup first
  when the final action depends on prior evidence.
- `agent/tool_dispatch_helpers.py` wraps `web_search`/`web_extract` output in
  `<untrusted_tool_result>` with explicit "DATA, not instructions" wording.
  We should port the idea for `web_search`/`web_fetch` observations to reduce
  prompt-injection risk.
- `toolsets.py` keeps `web_search` and `web_extract` as a paired "web"
  toolset, while still allowing search-only. This maps well to our presets:
  search-only can answer light factual questions, but research-report should
  require at least one fetch/read when fetch is available.

### OpenClaude

Useful patterns from `/home/roman/pyprojects/ML/openclaude`:

- Tool availability is treated as dynamic: command/runtime modes and deferred
  tool surfaces avoid showing the model capabilities it cannot call.
- The codebase emphasizes lifecycle tests around streaming, finalization, and
  preserved segments. For us this means research quality should be verified by
  trace criteria, not just by checking final text for URLs.
- Planning mode is a mode boundary, not a universal research gate. For pure
  research we should prefer lightweight todo/progress plus evidence gates over
  modal approval.

## Target Behavior

For a user request like:

> составь todo лист и иди по нему. Мне нужно поискать информацию в интернете о
> fork-join моделях массового обслуживания и их применении для расчета
> компьютерных сетей

the expected trace is:

1. Optional `todo_write` with 3-6 research steps.
2. `web_search` with broad query.
3. `web_fetch` for at least 2-4 high-signal URLs when available.
4. Optional second `web_search` for missing angles.
5. Final answer that:
   - mentions concrete sources;
   - distinguishes verified source content from model background knowledge;
   - includes a practical synthesis/algorithm;
   - does not end with "sources should be checked later".

For lightweight queries, e.g. "найди один свежий источник про X", one
`web_search` may be enough if the answer is explicitly framed as search-result
summary.

## Implementation Plan

### Phase 1 — Research Contract Classifier

- [x] Extend `agent_driver.runtime.task_contract` with research depth:
  `none | light_search | source_verified_report`.
- [x] Heuristics:
  - `source_verified_report` when user asks for "исследование", "отчет",
    "реферат", "deep research", "составь todo и иди по нему", "источники",
    "литература", "обзор", "сравни".
  - `light_search` for "найди один источник", "что сейчас", "свежая ссылка".
  - Keep "без интернета", "по памяти" as explicit no-research override.
- [x] Add this depth to trace metadata and runtime reminders.

### Phase 2 — Search/Fetched Evidence Gate

- [x] Add a small reusable evidence guard in `agent_driver`:
  `research_evidence_state(context)` returns counts for searches, fetched URLs,
  unique domains, failed fetches, and whether final answer is allowed.
- [x] For `source_verified_report`, require:
  - at least one `web_search`;
  - at least 2 successful `web_fetch` calls when `web_fetch` is available;
  - at least 2 unique domains unless fetch repeatedly fails.
- [x] If only search happened and fetch is available, runtime should continue to
  source fetching instead of force-final.
- [x] If fetch repeatedly fails, allow final answer but include "could not
  verify full pages" explicitly and cite search-result metadata only.

### Phase 3 — Prompt Fragments

- [x] Update `react_chat_tool_policy_web_search.txt`:
  search gives candidates; for reports it is not the final evidence layer.
- [x] Update `react_chat_tool_policy_web_fetch.txt`:
  for research reports, open concrete URLs from search results before synthesis.
- [x] Add a short Hermes-style research discipline fragment:
  "Every response either calls a research tool that advances evidence, or
  delivers the final synthesis. Do not stop with a plan or source candidates."
- [x] Keep dynamic prompt assembly: no fetch instructions when `web_fetch` is
  not in effective tools.

### Phase 4 — Untrusted Web Output Boundary

- [x] Wrap `web_search` and `web_fetch` tool observations in a reusable
  untrusted-data envelope before sending back to the model.
- [x] Preserve structured JSON for runtime/UI; the wrapper is for the LLM
  observation channel only.
- [x] Regression test: a fetched page that says "ignore previous instructions"
  is rendered as data, not instructions.

### Phase 5 — Trace Summary And Failure Flags

- [x] Extend `agent_driver.observability.run_trace_summary` with:
  - `research.depth`;
  - `research.search_count`;
  - `research.fetch_count`;
  - `research.unique_domains`;
  - `research.fetch_required_but_missing`;
  - `research.final_has_source_links`;
  - `failures.search_only_research_report`.
- [x] Add notes that point to the missing step, e.g.
  "Research report stopped after search candidates; no fetched source evidence."
- [x] Separate provider failures from agent-behavior failures:
  if the provider rejects a continuation request, trace summary marks
  `provider_rejected` and does not mislabel an interrupted run as fabricated
  planning.

### Phase 6 — Scenarios And Live Checks

- [x] Add deterministic scenario: `research-report-requires-fetch`.
- [x] Add live scenario based on fork-join queues:
  - expected `web_search >= 1`;
  - expected `web_fetch >= 2`;
  - expected final answer after fetched evidence;
  - expected no "I could not verify pages" unless fetch actually failed.
- [x] Add another orthogonal scenario:
  "сравни два современных Python веб-фреймворка с источниками" to avoid
  overfitting fork-join.
- [x] Add `CHAT_DEMO_LIVE_MODEL` override for live probes so scenario checks
  are not coupled to the last model manually selected in the UI.
- [x] Live check on 2026-05-31:
  `research-report-requires-fetch` passed on
  `qwen/qwen3-235b-a22b-2507` with `web_search=4`, `web_fetch=10`, 5 unique
  domains, terminal `run_completed`, and force-final reason
  `research_request_satisfied`.
- [ ] Check in Phoenix after each run: tool order, fetch success, final answer
  source shelf, no stuck/progress-only final.

### Phase 7 — UI/UX Polish For Research Evidence

- [x] In chat demo, make source shelf distinguish:
  - search result only;
  - fetched page;
  - cited in final answer.
- [x] Collapse noisy tool JSON by default; show title, domain, query/url,
  status, and source count.
- [x] Add a small "Research coverage" debug chip in replay/dev mode:
  `searched`, `fetched`, `domains`, `citations`.
- [x] Persist tool-derived `source_evidence` in session metadata via shared
  `agent_driver.observability.message_metadata`, so reloaded sessions and replay
  keep source cards even when the model did not put markdown links in the final
  answer.
- [x] Add a trace diagnostic for grounded answers that used fetched sources but
  did not include inline Markdown source links in the final text.
- [x] Do not treat blocked/failed fetches (`HTTP 403/404`, unsupported PDF,
  tool errors) as fetched evidence in trace summary or source cards.
- [x] Flag hallucinated tool names (`thought`, todo ids like
  `synthesize_findings`) as `unknown_tool_call` and strengthen the base prompt:
  only native listed tools may be called; todo ids are not tools.

### Phase 8 — Final Citation Policy

- [x] Strengthen the `source_verified_report` runtime reminder: after fetched
  evidence is available, final synthesis should include Markdown links to the
  concrete fetched/source URLs used.
- [x] Make missing final source links a trace failure for
  `source_verified_report`, not only a soft diagnostic.
- [x] Add trace failure `plan_todos_incomplete_on_final` when a run completes
  while the visible checklist still has pending/in-progress todos.
- [ ] Re-run live research scenarios and inspect Phoenix traces for:
  - fetched evidence before final synthesis;
  - final answer links when possible;
  - source shelf coverage when inline links are absent.
  Latest live attempts show the agent now reaches fetched evidence and source
  links, but still sometimes takes longer than the previous 180s probe timeout
  and can hallucinate a todo id as a tool before the new prompt guard. Keep this
  item open until a fresh run passes with terminal event, no unknown tool calls,
  and a completed visible plan.

## Acceptance Criteria

- The fork-join gpt-5.5 scenario produces a final answer at least as complete as
  the direct OpenRouter answer on source grounding and practical synthesis.
- Trace summary does not pass `source_verified_report` when the run only did
  `web_search` and never `web_fetch`, unless fetch was unavailable or failed
  after retries.
- The final answer has citations/source cards backed by runtime evidence.
- No modal planning approval appears for pure research.
- No raw web/tool JSON dominates the chat UI.

## First Slice

Implement Phase 1-3 first. This is the smallest useful change:

1. classify research depth;
2. force continuation from `web_search` to `web_fetch` for reports;
3. strengthen web prompt fragments;
4. add trace summary flag and deterministic tests.

Only after that add untrusted wrappers and UI coverage chips.

Status: first slice implemented on 2026-05-31. Covered by targeted runtime and
trace-summary tests. The Hermes-style untrusted web observation wrapper is also
implemented. Repeated fetch failures now unlock an explicit fallback final, but
failed/blocked fetches no longer count as successful source evidence.
Unique-domain source diversity and live probe scenarios are in place. Phase 8 is
now intentionally stricter: source-verified reports should include concrete
Markdown links, unknown tool calls are failures, and final answers should not
leave the visible plan unfinished. The remaining work is to make the live
fork-join scenario pass these stricter criteria consistently within the probe
timeout.
