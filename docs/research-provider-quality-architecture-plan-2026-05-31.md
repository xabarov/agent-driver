# Research Provider Quality Architecture Plan

Дата: 2026-05-31.

Этот план расширяет
[research-quality-improvement-plan-2026-05-31.md](research-quality-improvement-plan-2026-05-31.md)
и фокусируется на более глубокой проблеме: research quality зависит не только
от `web_search -> web_fetch`, но и от provider semantics, streaming,
reasoning/tool-call совместимости, recovery loops и качества engine contracts.

## Guiding Principles

- Держим курс Python Zen: простая модель, ясные контракты, проверяемое
  поведение.
- Сначала пробуем связку `model + prompt + small runtime guard + trace gate`.
- Новый DAG/state-machine слой добавляем только если Phoenix traces показывают
  повторяемый класс сбоев, который невозможно стабильно закрыть контрактами.
- Chat demo остается витриной: reusable provider/research/runtime logic живет в
  `agent_driver`, demo только визуализирует и проверяет.

## Current Failure Classes

- Research turn может завершиться после одного успешного источника, хотя
  пользователь просил todo/list и идти по нему.
- Модель может оставить visible plan/todo незавершенным и выдать финальный
  ответ.
- При provider-specific моделях возможны silent stalls, 402/4xx, long streaming
  без понятной terminal state в UI.
- Некоторые модели hallucinate tool names (`read_url`, `synthesize_findings`,
  `thought`) вместо использования реального `web_fetch`, `todo_write`,
  `python`.
- Search result иногда считается evidence, хотя для reports нужен fetched/read
  evidence.
- Provider/model picker и runtime пока недостаточно явно отражают capabilities:
  reasoning, tool calling, streaming reliability, context/output limits,
  provider quirks.

## External Findings

- Anthropic Claude Code best practices рекомендуют Plan Mode для сложных,
  multi-file/unclear задач, но не как универсальный режим; для простых задач
  planning overhead вреден. Это подтверждает наш подход с task contract, а не
  force-DAG для всего
  ([Claude Code Best Practices](https://www.anthropic.com/engineering/claude-code-best-practices),
  [Claude Code docs: best practices](https://code.claude.com/docs/en/best-practices)).
- Claude Code subagents описаны как отдельные context windows с
  task-specific prompts/tools, которые вызываются автоматически по описанию или
  явно пользователем. Для нас это аргумент в пользу всегда доступного
  delegation tool, но с хорошим prompt/tool contract и trace UI
  ([Anthropic Subagents](https://docs.anthropic.com/en/docs/claude-code/sub-agents)).
- Anthropic tool guidance: tool description должна объяснять, что tool делает,
  когда ее использовать, параметры и caveats; при большом tool surface помогает
  dynamic tool search/loading. Это поддерживает наш dynamic prompt assembly и
  tool-aware fragments вместо монолитного prompt
  ([Anthropic Define tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools),
  [Tool Search](https://code.claude.com/docs/en/agent-sdk/tool-search)).
- Anthropic engineering пишет, что tools с пересекающимися или размытыми
  задачами путают agents; лучше selective tool design, естественные имена и
  eval loop. Для нас это означает: не плодить `read_url`, `fetch_url`,
  `web_extract` aliases без необходимости; лучше чинить prompt/schema/repair
  вокруг canonical tools
  ([Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)).
- OpenAI Responses web search возвращает `web_search_call` items и URL
  annotations; UI должен показывать inline citations clearly visible/clickable.
  Это подтверждает наш source shelf + final links contract
  ([OpenAI Web search](https://platform.openai.com/docs/guides/tools-web-search?api-mode=responses)).
- OpenRouter отдельно документирует `reasoning_details` и streaming/tool-call
  event handling. Значит provider layer должен сохранять provider-specific
  reasoning/tool metadata, а не терять его при multi-turn continuation
  ([OpenRouter reasoning tokens](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens),
  [OpenRouter tool calling](https://openrouter.ai/docs/guides/features/tool-calling),
  [OpenRouter streaming SDK](https://openrouter.ai/docs/sdks/call-model/streaming)).

## Neighbor Project Findings

### Hermes

- `agent/prompt_builder.py` разделяет general identity, tool-use enforcement,
  model-family execution guidance and prerequisite checks. Самое полезное:
  response должен либо делать tool progress, либо давать final result; нельзя
  останавливаться на обещании.
- `agent/tool_guardrails.py` имеет pure controller для repeated failed/no
  progress tool calls. Это хороший стиль для `agent_driver`: маленький
  side-effect-free classifier, runtime решает, станет ли это warning,
  synthetic observation или halt.
- `agent/tool_dispatch_helpers.py` держит parallelism gating и список
  read-only/parallel-safe tools. Для research это пригодится позже, если
  понадобится параллельное fetch чтение; до trace signal не внедряем.
- `agent/tool_result_classification.py` показывает принцип: результат tool
  считается landed/evidence только если payload доказывает успех. Это ровно
  то, что мы уже начали делать для failed `web_fetch`.
- Hermes tests покрывают provider quirks: reasoning echo, provider fallback,
  malformed/unknown tool calls, web provider registry. Нам нужен такой же
  минимум вокруг OpenRouter/OpenAI-compatible providers.

### OpenClaude

- `python/smart_router.py` хранит provider health, latency, error rate и
  fallback strategy. Нам не нужен полный router немедленно, но нужна reusable
  `ProviderCapabilityProfile` и traceable provider outcome.
- `src/tools/WebSearchTool/providers/index.ts` держит web provider chain:
  `auto` falls through, explicit provider fails loudly. Это хороший UX/engine
  контракт: auto can recover; explicit mode must surface the real failure.
- `src/query/toolFailureLoopGuard.ts` классифицирует repeated tool failures by
  signature/category/path. Это полезнее, чем просто counting tool calls.
- `src/tools/WebFetchTool/prompt.ts` делает fetch результат secondary-model
  extraction with quote limits and a cache. Для agent-driver можно оставить
  simple fetch сейчас, но source extraction contract стоит вынести в
  reusable research layer.
- `src/constants/prompts.ts` показывает полезный системный принцип:
  tool results may contain external data and prompt injection; reminders should
  be layered, not dumped в один большой prompt.
- Provider profiles/model picker в OpenClaude отделяют configured provider,
  base URL, model, auth/header quirks. Это прямо связано с нашим багом “модель
  выбрана, а key/capabilities/provider behavior неочевидны”.

## Architecture Direction

### 1. Provider Capability Profile

Добавить в `agent_driver` lightweight provider/model profile:

- `provider_id`, `model_id`, `base_url_family`;
- supports: `streaming`, `tool_calls`, `parallel_tool_calls`, `reasoning`,
  `reasoning_echo`, `web_native`, `json_schema`, `max_output_tokens`;
- observed health: last status, last error category, rolling latency/timeout;
- request knobs: `extra_body`, reasoning flags, timeout class.

Важно: это не smart-router v1. Это shared truth for request building,
diagnostics, model picker UI and trace summary.

### 2. Research Session Contract

Ввести маленький runtime object поверх текущих message observations:

- task depth: `none | light_search | source_verified_report`;
- evidence ledger: searches, successful fetches, failed fetches, unique
  domains, cited URLs, negative evidence;
- visible plan state: pending/in_progress/done;
- final readiness: `allowed`, `repair_needed`, `blocked_by_provider`.

Это не DAG: contract вычисляется из истории и используется для reminders,
trace summary and bounded repair.

### 3. Bounded Repair Turn

Если модель пытается завершить `source_verified_report`, но contract нарушен:

- missing fetched evidence while fetch available;
- missing final source links after fetched evidence;
- incomplete visible todo;
- unknown internal-looking tool name.

Делаем максимум один bounded repair turn с коротким synthetic observation:
что именно нарушено и какие реальные tools доступны. Если после repair снова
нарушение, финализируем честным failure/partial response вместо бесконечного
цикла.

### 4. Tool Call Repair / Guardrails

Вдохновлено Hermes/OpenClaude:

- classify unknown tools: typo/alias (`read_url`), internal thought tool,
  todo-id-as-tool, genuinely unavailable;
- for obvious alias, do not silently execute different tool; return corrective
  tool observation: "`read_url` is not available; use `web_fetch`";
- repeated same unknown/failing tool trips a guardrail and forces strategy
  change or partial final.

### 5. Provider Failure Semantics

Разделить failures:

- user/API billing/auth (`402`, `401`, `403`) -> terminal provider error with
  UI message, no fake “Writing” forever;
- stream idle timeout -> controlled continuation or provider_stalled;
- provider rejects continuation due reasoning/tool echo -> provider quirk with
  retry policy only when profile says retry is safe;
- model selected but unsupported capability -> UI warning before run.

### 6. Live Evaluation Loop

Расширить Phoenix/Playwright scenario suite:

- fork-join research report;
- “todo and go through it” research task;
- two-framework comparison with sources;
- no-web/source-memory-only prompt;
- provider auth/billing failure;
- unknown-tool recovery (`read_url`/`synthesize_findings`);
- long streaming/stall timeout;
- reasoning model continuation with preserved provider metadata.

Each scenario must store:

- transcript excerpt;
- trace summary JSON;
- provider profile used;
- pass/fail reasons;
- screenshot for UI regressions.

## Implementation Phases

### Phase A — Provider Profile Foundation

- [x] Add `agent_driver.llm.provider_capabilities` with static profiles and
  safe defaults for unknown OpenAI-compatible providers.
- [x] Teach OpenRouter/OpenAI-compatible request builder to expose selected
  profile in run metadata/trace summary.
- [x] Preserve provider-specific reasoning metadata when present, without
  leaking chain-of-thought to UI.
- [x] Add backend tests for Qwen/OpenRouter/GPT profile defaults.
- [x] Extend profile usage into chat model picker capability warnings and
  provider failure UX.

### Phase B — Research Contract Object

- [x] Extract research readiness from scattered helpers into
  `ResearchSessionContract`.
- [x] Include visible todo completeness and source evidence completeness in one
  readiness result.
- [x] Add trace summary fields:
  `final_readiness`, `repair_required_reasons`, `provider_profile`.
- [x] Keep chat demo unchanged except consuming new metadata.

### Phase C — Bounded Repair

- [x] Add one bounded repair continuation for final answers that violate
  research/todo/source contract.
- [x] Add deterministic tests:
  missing links -> repair reminder;
  incomplete todo -> repair reminder;
  second violation -> terminal partial/failure with clear reason.
- [x] Verify no infinite loops and no modal planning approval for pure research.

### Phase D — Unknown Tool Guardrail

- [x] Add unknown-tool classifier:
  `read_url -> unavailable_alias_for_web_fetch`,
  `synthesize_findings -> todo_id_or_internal_step`,
  `thought/reason/scratchpad -> hidden_reasoning_tool`.
- [x] Surface corrective observation to the model and trace failure to Phoenix.
- [x] Add repeated unknown-tool guard inspired by OpenClaude
  `toolFailureLoopGuard`.

### Phase E — Provider Failure UX

- [x] Replace stuck “Writing” on provider 4xx/stream error with terminal chat
  error card and retry hint.
- [x] Model picker should expose capability/health warnings:
  “tool calls unknown”, “reasoning metadata unsupported”, “last request 402”.
- [x] Fix model list loading/search so popular models after `G` are discoverable
  and not hidden by pagination/search state.

### Phase F — Live Phoenix Gate

- [x] Run the expanded scenario suite on default Qwen and one reasoning model.
- [x] Mark a scenario successful only when:
  terminal event exists;
  no unknown tools;
  required source evidence exists;
  visible plan/todo is complete or intentionally not used;
  final answer includes citations/cards when research used web.
- [x] If Phase A-D still leave repeated failures, design a minimal
  state-machine runner for research only. Do not generalize to all tasks until
  traces justify it.

Phase F result: live probes passed on the running dev stack for
`simple-direct`, `python-count-letters`, `research-report`,
`subagent-explicit-delegation`, and the heavy
`research-report-requires-fetch` scenario. The first heavy run exposed two
runtime issues: forced-final blocked repair tools, and source cards were not
counted as final citations in trace summary. Both were fixed without adding a
research DAG/state-machine layer.

## Acceptance Criteria

- Fork-join report on live chat completes with at least two successful fetched
  sources or a clear provider/source failure, never a silent partial answer.
- `todo and go through it` tasks cannot pass trace summary with unfinished
  todos unless the final explicitly explains a blocker.
- Unknown tool names do not silently degrade into weak final answers.
- Provider 402/401/timeout cannot leave UI in indefinite `Writing`.
- Provider/model capabilities are visible in traces and reusable outside chat
  demo.
- No new chat-demo-only engine logic.
