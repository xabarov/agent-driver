# Provider And Model Debugging

Status: active live-gate playbook. Keep current while the provider/model matrix
is changing.

Дата: 2026-05-31.

Эта страница фиксирует практику поиска provider/model проблем для
`agent-driver`, особенно когда chat-demo ведет себя по-разному на разных
OpenRouter моделях.

## Why This Exists

Research качество зависит не только от prompt/runtime contract. Для
OpenRouter-compatible моделей важны:

- model-specific tool calling behavior;
- reasoning/tool-call continuation format;
- streaming vs non-streaming deltas;
- provider-specific request parameters;
- реальные 4xx/5xx semantics.

Нельзя считать сценарий закрытым только потому, что он прошел на одной модели.
Перед изменениями в provider/research слое нужно проверять документацию и
минимум одну live-модель из целевого класса.

## Required Preflight

Перед live проверкой новой или проблемной OpenRouter модели:

1. Открыть OpenRouter quickstart:
   <https://openrouter.ai/docs/quickstart>
2. Проверить OpenRouter tool-calling docs:
   <https://openrouter.ai/docs/guides/features/tool-calling>
3. Для reasoning-моделей проверить reasoning tokens / preservation:
   <https://openrouter.ai/docs/guides/best-practices/reasoning-tokens>
4. Проверить страницу модели в OpenRouter UI: tool support, structured output,
   context/output limits, provider health/tool-call error rate.
5. Если доступен Context7, запросить OpenRouter docs через MCP вместо ручного
   поиска. API keys не записывать в repo/docs/logs.

## Live Debug Loop

### Model + Web Tool Preflight

Перед `research-report-requires-fetch` новая модель должна пройти дешевую
лестницу. Это отделяет provider/model/tool-call проблемы от полного research
контракта:

1. `simple-direct` — обычный текст без web/tools.
2. `model-preflight-web-search` — модель вызывает `web_search`, но не
   `web_fetch`.
3. `model-preflight-web-fetch-direct` — модель открывает известный URL через
   `web_fetch` без поиска.
4. `model-preflight-search-fetch` — модель делает минимальный
   `web_search -> web_fetch -> final`.
5. `research-report-requires-fetch` — полный research/todo/source-verified
   сценарий.

Запуск ladder:

```bash
CHAT_DEMO_URL=http://localhost:5174 \
CHAT_DEMO_LIVE_MODEL='deepseek/deepseek-v4-flash' \
CHAT_DEMO_LIVE_ARTIFACT_DIR=/tmp/chat-demo-live-preflight-deepseek \
  .venv/bin/python examples/chat-demo/frontend/tests/e2e/chat_live_probe.py \
  --model-preflight
```

Если модель падает на шагах 1-4, не запускать дорогой full research: сначала
исправить route/tool-calling/web-tool слой и записать run id.

Для полного `research-report-requires-fetch` live-probe теперь имеет
cost-safety guard: если run набрал 10 `web_search` или 10 `web_fetch`, но все
еще не достиг минимальной source diversity (`min_research_domain_count=2`),
probe ставит `probe_budget_stop`, вызывает
`POST /api/chat/runs/{run_id}/cancel` и сохраняет причину в
`trace-summary.json`. Такой stop считается fail, но это диагностический fail:
он дешевле и полезнее, чем ждать 10-минутный timeout при source-diversity loop.

### Full Scenario

1. Запустить конкретный scenario:

```bash
CHAT_DEMO_URL=http://localhost:5174 \
CHAT_DEMO_LIVE_MODEL='openai/gpt-5.5' \
CHAT_DEMO_LIVE_ARTIFACT_DIR=/tmp/chat-demo-live-gpt55 \
  .venv/bin/python examples/chat-demo/frontend/tests/e2e/chat_live_probe.py \
  --scenario research-report-requires-fetch
```

2. Сохранить run id и артефакты:
   `/tmp/chat-demo-live*/<scenario>/trace-summary.json`,
   `transcript-excerpt.txt`, `screenshot.png`.
3. Проверить `/api/chat/runs/{run_id}/trace-summary`:
   terminal event, `provider_rejected`, tool order, `research.fetch_count`,
   `final_readiness`, unfinished todos.
4. Проверить Phoenix `http://localhost:6006`, project
   `agent-driver-chat-demo`, и сравнить spans с persisted event log.
5. Если provider вернул 400 после tool results, проверить wire protocol:
   assistant `tool_calls`, matching tool messages, `tool_choice`, reasoning
   echo, truncation/repair messages.

## Cost Discipline

Не начинать research-отладку с дорогой frontier-модели. Порядок:

1. Проверить контракт и явные provider 4xx на самой дешевой подходящей модели.
2. Перейти к 1-2 средним моделям из другого семейства.
3. Запускать `openai/gpt-5.5` только как финальный acceptance-gate или когда
   проблема воспроизводится только на GPT-5.x reasoning/tool continuation.

Если OpenRouter возвращает `402` про credits/max_tokens, это не research
регрессия. Сохраняем run id, снижаем output budget и продолжаем отладку на
дешевой модели.

Перед live matrix запуском проверить цены и доступность через OpenRouter models
API: <https://openrouter.ai/api/v1/models>. Цены меняются, поэтому не
полагаться на старую таблицу без повторной проверки.

## Current Finding: OpenRouter GPT-5.5

Сценарий:

`research-report-requires-fetch`

Prompt:

`составь todo лист и иди по нему. Мне нужно поискать информацию в интернете о fork-join моделях массового обслуживания и их применении для расчета компьютерных сетей`

Observed failures:

- `run_f125336d0b6c`: `todo_write`, 6 `web_search`, 0 `web_fetch`,
  provider HTTP 400, `search_only_research_report`.
- `run_46da9575fce2`: forced next tool `web_fetch` reached the third LLM
  request, but OpenRouter still returned HTTP 400 before any `web_fetch`
  tool call.
- `run_45d3489ff64b`: after reasoning block compaction, forced `web_fetch`
  still received HTTP 400.
- `run_ce6e9efbf515`: schema/reasoning fixes allowed search/fetch progress,
  but OpenRouter returned HTTP 402; runtime now retries with a smaller
  `max_tokens`.
- `run_a9474a3c7140`: no provider rejection; the remaining failure is research
  quality (`web_fetch` happened, but source diversity/todos were incomplete).

Hypotheses already tested:

- Search-only source shelf was UI misleading; fixed by labeling search-only
  evidence as `Search candidates`.
- Runtime now forces next `tool_choice` to `web_fetch` for
  `source_verified_report` after search-only evidence.
- OpenRouter reasoning details are preserved and echoed on assistant tool-call
  messages.
- Streaming reasoning summary/text chunks are merged before echoing.
- Tool JSON schemas are normalized before provider send; arrays without
  `items` caused OpenRouter/Azure 400 on `web_search.mock_results`.
- Post-trim protocol repair inserts stub tool results when trimming would leave
  an assistant `tool_calls` message without matching `tool` output.
- Provider 402 from output-budget/credit pressure is retried with fewer
  `max_tokens`.

Current state:

- The original `search-only -> provider 400` class is fixed for the fork-join
  research scenario.
- The model+web preflight ladder is green on DeepSeek, GLM, Kimi, Qwen 3.7,
  Claude Sonnet 4.6, and GPT-5.5 final acceptance.
- GPT-5.5 remains an acceptance gate, not the first debugging target. Future
  provider/research changes should still start with cheaper models.
- Text regex heuristics no longer force Python or fail runs with
  `missed_python`; Python usage is prompt-guided and asserted only by scenarios
  that explicitly set `required_tools=("python",)`.

Next engineering action:

- For new provider/research changes, run the broad deterministic regression,
  then the cheap-to-expensive live ladder only when the deterministic signal is
  clean. Do a cost-reduction pass only when fresh traces show a tool-heavy
  spend loop.

## Model Matrix

Run the `--model-preflight` ladder for each model before treating
`research-report-requires-fetch` as meaningful. Approximate prices below were
read from OpenRouter models API on 2026-05-31; re-check before spending.

- `deepseek/deepseek-v4-flash`
- `qwen/qwen3.5-flash-02-23`
- `z-ai/glm-4.7`
- `moonshotai/kimi-k2.6`
- `qwen/qwen3.7-max`
- `anthropic/claude-sonnet-4.6`
- `openai/gpt-5.5`

This order is intentional: start cheap/fast, then move toward stronger and
more expensive models only after the research contract is stable.

Observed prices from the same API snapshot:

| Model | Prompt | Completion | Context |
| --- | ---: | ---: | ---: |
| `qwen/qwen3.5-flash-02-23` | `0.000000065` | `0.00000026` | `1000000` |
| `deepseek/deepseek-v4-flash` | `0.0000000983` | `0.0000001966` | `1048576` |
| `z-ai/glm-4.7` | `0.0000004` | `0.00000175` | `202752` |
| `moonshotai/kimi-k2.6` | `0.000000684` | `0.00000342` | `262144` |
| `qwen/qwen3.7-max` | `0.00000125` | `0.00000375` | `1000000` |
| `anthropic/claude-sonnet-4.6` | `0.000003` | `0.000015` | `1000000` |
| `openai/gpt-5.5` | `0.000005` | `0.00003` | `1050000` |

Live results:

| Date | Model | Run | Result | Notes |
| --- | --- | --- | --- | --- |
| 2026-05-31 | `qwen/qwen3.5-flash-02-23` | `run_314f25303527` | blocked | OpenRouter returned 402 insufficient credits before first LLM completion. |
| 2026-05-31 | `qwen/qwen3.5-flash-02-23` | `run_5b90cf503e36` | blocked | After key replacement/container recreate, OpenRouter returned model rate limit before first LLM completion. |
| 2026-05-31 | `qwen/qwen3.5-flash-02-23` | `run_fc622a5149d5` | blocked | Re-test after runtime fixes still failed before first completion: OpenRouter HTTP 429 `Provider returned error`. Stop testing this route for now. |
| 2026-05-31 | `qwen/qwen3.7-max` | `run_0a2ae585a389` | preflight pass | `simple-direct`: no tools. |
| 2026-05-31 | `qwen/qwen3.7-max` | `run_95ef3f5fb3c5` | preflight pass | `model-preflight-web-search`: 2 `web_search`, no fetch required. |
| 2026-05-31 | `qwen/qwen3.7-max` | `run_f6bbda6b446e` | preflight pass | `model-preflight-web-fetch-direct`: 2 `web_fetch`, `fetch_required=true`. |
| 2026-05-31 | `qwen/qwen3.7-max` | `run_23949a99d5d7` | preflight pass | `model-preflight-search-fetch`: 2 `web_search`, 2 `web_fetch`, `fetch_required=true`. |
| 2026-05-31 | `qwen/qwen3.7-max` | `run_0748cf4771b9` | fail | Full research hit the GPT-5.5-shaped failure: search-only evidence, then OpenRouter HTTP 400 before `web_fetch`. Runtime now retries forced named tool-choice rejections without hard `tool_choice`. |
| 2026-05-31 | `qwen/qwen3.7-max` | `run_87d4066b6784` | stopped | Removing forced `tool_choice` avoided provider 400, but with the full catalog visible Qwen kept calling `web_search`; live-probe budget-stop cancelled at 12 search / 0 fetch. |
| 2026-05-31 | `qwen/qwen3.7-max` | `run_326d9bc1f456` | stopped | Re-test still search-looped before proactive catalog narrowing was applied. |
| 2026-05-31 | `qwen/qwen3.7-max` | `run_6c135c4f0682` | pass | Full research after proactive forced-tool catalog narrowing: 10 search, 11 fetch, 4 domains, `final_readiness=allowed`, no failure flags. |
| 2026-05-31 | `anthropic/claude-sonnet-4.6` | `run_a96e340e187b` | preflight pass | `simple-direct`: no tools. |
| 2026-05-31 | `anthropic/claude-sonnet-4.6` | `run_4f92505caeaf` | preflight pass | `model-preflight-web-search`: 2 `web_search`, no fetch required. |
| 2026-05-31 | `anthropic/claude-sonnet-4.6` | `run_2665181249dc` | preflight pass | `model-preflight-web-fetch-direct`: 2 `web_fetch`, `fetch_required=true`. |
| 2026-05-31 | `anthropic/claude-sonnet-4.6` | `run_9858e5e41a5d` | preflight pass | `model-preflight-search-fetch`: 2 `web_search`, 2 `web_fetch`, `fetch_required=true`. |
| 2026-05-31 | `anthropic/claude-sonnet-4.6` | `run_a456373785f7` | pass | Full research: 6 search, 4 fetch, 2 domains, `final_readiness=allowed`, no failure flags. Cleanest mid/frontier compatibility result so far. |
| 2026-05-31 | `z-ai/glm-4.7` | `run_2255e3e88629` | preflight pass | `simple-direct`: no tools. |
| 2026-05-31 | `z-ai/glm-4.7` | `run_5e43f7df98cb` | preflight pass | `model-preflight-web-search`: 2 `web_search`, no fetch required. |
| 2026-05-31 | `z-ai/glm-4.7` | `run_c20a500ef3c4` | preflight pass | `model-preflight-web-fetch-direct`: 2 `web_fetch`, `fetch_required=true`. |
| 2026-05-31 | `z-ai/glm-4.7` | `run_cf4059ed8d1a` | preflight pass | `model-preflight-search-fetch`: 2 `web_search`, 2 `web_fetch`, `fetch_required=true`. |
| 2026-05-31 | `z-ai/glm-4.7` | `run_f3583d684403` | near-pass | Full research collected enough evidence: 22 search, 15 fetch, 5 domains. Near-tool-budget forced final emitted a huge text-form `todo_write` and then hit `max_steps_exceeded`; runtime now retries tool-call-shaped forced finals with tools disabled. |
| 2026-05-31 | `z-ai/glm-4.7` | `run_0bcc0235d244` | pass | Full research after forced-final retry fix: 6 search, 13 fetch, 3 domains, `final_readiness=allowed`, no failure flags. |
| 2026-05-31 | `z-ai/glm-4.7` | `run_2810f82eed54` | fail | Budget guard reduced early research to 2 search / 10 fetch, but GLM emitted XML-ish text-form tool calls during forced final. Parser now supports `<arg_key>/<arg_value>` blocks and suppressed text-form forced finals trigger no-tools retry. |
| 2026-05-31 | `z-ai/glm-4.7` | `run_66b5747d06da` | stopped | After narrowing fetch-failure fallback, runtime no longer finalized with one successful domain. The run entered a source-diversity cost loop: 10 search, 7 fetch, still only `en.wikipedia.org`; harness was stopped manually to avoid spend. Next fix should target domain-aware search/fetch de-duplication. |
| 2026-05-31 | `z-ai/glm-4.7` | `run_f5baa71005e8` | pass | Full research after domain-aware diversity repair: 2 search, 4 fetch, 2 domains, `final_readiness=allowed`, no failure flags. |
| 2026-05-31 | `moonshotai/kimi-k2.6` | `run_115c9e21eb5c` | preflight pass | `simple-direct`: no tools. |
| 2026-05-31 | `moonshotai/kimi-k2.6` | `run_b2addba7a6ee` | preflight pass | `model-preflight-web-search`: 2 `web_search`, no fetch required. |
| 2026-05-31 | `moonshotai/kimi-k2.6` | `run_78fbafc9e973` | preflight pass | `model-preflight-web-fetch-direct`: 2 `web_fetch`, `fetch_required=true`. |
| 2026-05-31 | `moonshotai/kimi-k2.6` | `run_1b9876fd8f6b` | preflight pass | `model-preflight-search-fetch`: 2 `web_search`, 2 `web_fetch`, `fetch_required=true`. |
| 2026-05-31 | `moonshotai/kimi-k2.6` | `run_20600e4625ba` | pass | Full research: 14 search, 15 fetch, 4 domains, `final_readiness=allowed`, no failure flags. |
| 2026-05-31 | `deepseek/deepseek-v4-flash` | `run_e91bdcbe3adc` | fail | Provider OK, but model ignored research tools; contract repair now forces `web_search`/`web_fetch`. |
| 2026-05-31 | `deepseek/deepseek-v4-flash` | `run_a1afd88a7bb2` | near-pass | Research evidence passed; remaining failure was unfinished visible todos. |
| 2026-05-31 | `deepseek/deepseek-v4-flash` | `run_d533cf8c602c` | near-pass | Research evidence passed and `todo_write` repair fired; final answer was empty/progress-only with synthesis todo still in progress. |
| 2026-05-31 | `deepseek/deepseek-v4-flash` | `run_8dde10ba3615` | fail | Meaningful sourced final answer existed, but `run_trace_summary` still treated stale research-process todos as failure. Summary contract now matches runtime. |
| 2026-05-31 | `deepseek/deepseek-v4-flash` | `run_7bb8eb4dbe9e` | fail | Forced final streaming and non-stream retry both returned empty `stop` with zero usage. Replaced runtime-authored synthesis fallback with a clean model retry that disables tools. |
| 2026-05-31 | `deepseek/deepseek-v4-flash` | `run_495abf7890b3` | fail | Historical runtime-authored fallback exposed a continuation bug; superseded by the clean no-tools model retry path. |
| 2026-05-31 | `deepseek/deepseek-v4-flash` | `run_c764e311d74a` | pass | `research-report-requires-fetch` passed: 8 search, 13 fetch, 2 domains, `final_readiness=allowed`, no failure flags. |
| 2026-05-31 | `deepseek/deepseek-v4-flash` | `run_d9988bd65896` | preflight pass | `simple-direct`: no tools, `final_readiness=allowed`. |
| 2026-05-31 | `deepseek/deepseek-v4-flash` | `run_1e436e0df0bb` | preflight pass | `model-preflight-web-search`: 2 `web_search`, no fetch required. |
| 2026-05-31 | `deepseek/deepseek-v4-flash` | `run_b5e4b4325afb` | preflight pass | `model-preflight-web-fetch-direct`: 2 `web_fetch`, `fetch_required=true`. |
| 2026-05-31 | `deepseek/deepseek-v4-flash` | `run_2f0f53dc2a33` | preflight pass | `model-preflight-search-fetch`: 2 `web_search`, 2 `web_fetch`, `fetch_required=true`. |
| 2026-05-31 | `deepseek/deepseek-v4-flash` | `run_ca617c3d0b62` | preflight pass | Full ladder final step: `research-report-requires-fetch`, 2 search, 23 fetch, 2 domains, `final_readiness=allowed`. |
| 2026-05-31 | `deepseek/deepseek-v4-flash` | `run_a555eeefa0e4` | fail | After removing runtime-authored synthesis, search/fetch preflights passed but full research hit empty forced-final retries. No-tools retry now appends an explicit final-answer reminder while still requiring model-authored output. |
| 2026-05-31 | `deepseek/deepseek-v4-flash` | `run_68e8cfe031a6` | fail | No-tools retry returned model tokens but runtime did not publish the non-stream retry text into UI/summary events. Runtime now emits `token_delta` + `assistant_message_replaced` for non-stream final retry content. |
| 2026-05-31 | `deepseek/deepseek-v4-flash` | `run_0c5ae3047935` | fail | Reasoning-disabled no-tools retry produced visible output, but stale todos still failed because the retry text was not visible to trace-summary. Fixed by publishing the retry content. |
| 2026-05-31 | `deepseek/deepseek-v4-flash` | `run_16aac1126f90` | preflight pass | Full ladder final step after fixes: 8 search, 23 fetch, 2 domains, `final_readiness=allowed`, no failure flags. |
| 2026-05-31 | `deepseek/deepseek-v4-flash` | `run_d5961935104d` | pass | Full research after budget guard: 8 search, 6 fetch, 3 domains, `final_readiness=allowed`, no failure flags. Guard forced final once research evidence was satisfied despite stale process todos. |
| 2026-05-31 | `openai/gpt-5.5` | `run_a9474a3c7140` | research failure | Provider protocol rejection fixed; residual failure was source diversity/todo completion before the later repair passes. |
| 2026-05-31 | `openai/gpt-5.5` | `run_657ce790e764` | pass | Final acceptance after cheap-to-expensive matrix: 6 search, 3 fetch, 2 domains, `final_readiness=allowed`, no failure flags. Original search-only/provider-400 illusion is fixed for this scenario. |

DeepSeek-specific behavior:

- It often emits empty assistant content with `tool_calls`; this is acceptable
  when native tool calls are present.
- It can return empty `stop` with zero usage on forced final answer, both in
  streaming and non-streaming OpenRouter routes.
- A direct OpenRouter probe showed that `deepseek/deepseek-v4-flash` may spend
  a small response budget entirely on reasoning tokens and return
  `content_len=0` unless reasoning is disabled for the final retry.
- Runtime now retries an empty forced-final stream once without streaming.
- If the non-stream retry is also empty, runtime retries once more with the
  tool catalog disabled, no `tool_choice`, and an explicit final-answer
  reminder appended to the conversation. For OpenRouter DeepSeek routes this
  retry also sends `reasoning: {"enabled": false, "exclude": true}` so visible
  `content` is produced. This keeps final synthesis model-authored while
  avoiding OpenRouter/model quirks around forced `tool_choice="none"`.
- Non-stream final retry content must be surfaced as normal assistant events
  (`token_delta` + `assistant_message_replaced`), otherwise UI/trace-summary
  still see the earlier empty streamed final.
- Source-diversity repair must force `web_search`; missing fetched-source
  repair forces `web_fetch`. Forcing `web_fetch` for source diversity caused
  expensive repeated fetches from the same domain.
- Once source-verified research evidence is satisfied, runtime should force a
  final answer even if visible process todos are stale. Trace-summary can treat
  a meaningful sourced final answer as covering those research-process todos.
  This reduced a DeepSeek full research run from 23 fetches to 6 fetches.
- Runtime no longer writes a source-backed report itself. If the clean model
  retry also returns empty, the run remains diagnosable instead of being
  silently converted into a runtime-authored answer.

GLM-specific behavior:

- `z-ai/glm-4.7` passed direct, search-only, fetch-direct, and search+fetch
  preflights.
- In full research it may emit text-form tool calls during forced final
  (`<tool_call>todo_write ...`) even with `tool_choice="none"`. Runtime now
  treats normalized `planned_tool_calls` during forced final as a reason for
  the same clean no-tools final retry used for empty forced finals.
- It can also emit XML-ish text-form calls such as
  `<tool_call>web_fetch<arg_key>url</arg_key><arg_value>...</arg_value></tool_call>`.
  The text-form parser now normalizes this shape, and suppressed text-form
  calls during forced final trigger the no-tools retry path.
- When source diversity is missing, GLM can repeatedly search/fetch without
  escaping one successful domain. Runtime now makes source-diversity repair
  domain-aware: after same-domain fetches it forces `web_search`, after a
  repair search it forces `web_fetch`, and the prompt names domains that should
  be avoided.
- The successful full run still used many tool calls (6 search, 13 fetch, 30
  total tool calls). This is acceptable for compatibility but should feed the
  cost-reduction track.

Qwen-specific behavior:

- `qwen/qwen3.7-max` passed direct, search-only, fetch-direct, and search+fetch
  preflights.
- In full source-verified research it can continue calling `web_search` even
  after the runtime repair asks for `web_fetch`.
- Hard forced named `tool_choice` may trigger OpenRouter HTTP 400 on the
  repair turn. Runtime now retries that provider rejection with the same
  conversation and no forced `tool_choice`.
- Removing `tool_choice` alone is not enough for Qwen: when the full catalog
  remains visible, it may search-loop until the live-probe budget-stop. Runtime
  now proactively narrows the visible tool catalog to the forced repair tool.
  If the provider honors `tool_choice`, the request is precise; if the provider
  rejects it and the retry removes `tool_choice`, the model still sees only the
  intended repair tool.
- Full research passed after catalog narrowing but remains tool-heavy: 10
  search, 11 fetch, 4 domains. Keep Qwen in the compatibility matrix; use
  cheaper models for cost-tuning first.

Claude-specific behavior:

- `anthropic/claude-sonnet-4.6` passed direct, search-only, fetch-direct,
  search+fetch, and full research through OpenRouter.
- Full research was the cleanest mid/frontier result so far: 6 search, 4 fetch,
  2 domains, no repair leftovers, no provider rejection. This is a good
  adjacent acceptance signal before spending on GPT-5.5.

GPT-5.5-specific behavior:

- The original failure pattern was reproduced as search-only evidence followed
  by provider rejection / misleading source shelf UI.
- After the research contract, source-diversity repair, forced-final cleanup,
  provider retry, and forced-tool catalog narrowing, `openai/gpt-5.5` passed
  `research-report-requires-fetch`: 6 search, 3 fetch, 2 domains.
- Keep using the cheap-to-expensive matrix before future GPT-5.5 runs. The
  model is now an acceptance gate, not the first debugging target.

## Change Set Summary

This slice groups into five reviewable themes:

1. Research contract and trace diagnostics:
   `research_depth`, `fetch_required`, fetched-source/source-diversity
   readiness, search-only failure flags, final-readiness repair reasons and
   trace-summary parity with runtime.
2. Provider/model compatibility:
   OpenRouter reasoning echo handling, tool schema normalization, reduced
   `max_tokens` retry for 402, forced named `tool_choice` rejection retry,
   forced-tool catalog narrowing and provider payload debug stats.
3. Clean finalization:
   empty forced-final stream retry, no-tools model retry for tool-call-shaped
   forced finals, non-stream retry output surfaced to UI/trace, and removal of
   runtime-authored source-backed synthesis.
4. Chat-demo evidence UX and live harness:
   search-only evidence is displayed as `Search candidates`, fetched/final links
   remain `Sources`, model+web preflight ladder, budget-stop cancellation for
   source-diversity loops and updated concept-smoke model fixtures.
5. Resume/planning safety:
   approved `exit_plan_mode_v2` plans resume into the LLM without
   re-executing the approval tool, stale force-final flags are cleared, and
   budget remains available for the next real work tool.

Known follow-up, not part of the correctness fix:

- Cost-reduction/tuning for compatible but tool-heavy models, especially Qwen
  and Kimi.
- Broader context-pressure plan from Unified Work Plan Phase 2.
- Runtime metadata inventory before larger SDK/refactor phases.

Kimi-specific behavior:

- `moonshotai/kimi-k2.6` passed direct, search-only, fetch-direct, search+fetch,
  and full research.
- Full research was reliable but tool-heavy: 14 search, 15 fetch, 36 total tool
  calls. This reinforces the need for a later budget/cost pass after
  compatibility is stable.

For each model record:

- date, model id, provider route if visible;
- pass/fail;
- run id;
- terminal event;
- tool path;
- `web_fetch` count/domain count for research;
- provider rejection status/body summary;
- whether reasoning echo was present/needed;
- artifact directory.

## Regression Rule

A research/provider fix is not done until:

- deterministic tests cover the local contract;
- the failing live model is retried;
- at least one adjacent model class is sampled when the fix touches
  OpenRouter/provider semantics;
- docs record what was learned.

## Validation Snapshot

After the final GPT-5.5 acceptance run on 2026-05-31:

- `pytest tests/llm tests/runtime tests/observability -q` passed.
- `pytest examples/chat-demo/backend/tests -q --import-mode=importlib` passed.
- `vitest --run` in `examples/chat-demo/frontend` passed.
- `chat_concepts_smoke.py` passed against `CHAT_DEMO_URL=http://localhost:5174`.
- `git diff --check` and focused `black --check` passed.

The combined pytest invocation for core + backend has a known module-name
collision between two `test_run_trace_summary.py` files; run backend tests in a
separate pytest process or use `--import-mode=importlib`.
