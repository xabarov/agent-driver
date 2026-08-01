# Research Quality Summary

Status: reference / status page. Keep this page for the completed baseline and
research evidence decision log; use
[Unified Work Plan](unified-work-plan-2026-05-31.md) and
[Efficient Deep Research Workspace Architecture](efficient-deep-research-workspace-architecture-2026-05-31.md)
for active next work.

Дата исходного плана: 2026-05-31.

Статус: базовый runtime contract и provider/model acceptance закрыты для
исходного fork-join сценария. 2026-05-31 после проверки реального chat-demo
сценария план был уточнен: provider failure после успешного `web_search`
создавал иллюзию выполненного research, потому что UI показывал search
candidates под заголовком `Sources`. Эта иллюзия исправлена, а финальная
GPT-5.5 acceptance-проверка прошла.

Класс сбоя:

`todo list -> web_search done -> provider HTTP 400 -> Run failed + Sources`

Это не выполненное исследование. `web_search` является только candidate
discovery, а verified research evidence начинается с успешного `web_fetch` или
явных ссылок в финальном ответе. Карточка failed-run может показывать найденные
кандидаты, но не должна называть их проверенными источниками.

Исправление 2026-05-31:

- Search-only source shelf в chat-demo переименован в `Search candidates`.
- Бейдж `web_search` в shelf переименован из `search` в `candidate`.
- `Sources` остается только для `web_fetch` и assistant/final links.
- Добавлен frontend test на failed assistant bubble с одними search candidates.

Оставшиеся проверки перенесены в общий live/Phoenix verification loop.

## Goal

Research-report задачи должны проходить цикл:

`web_search -> web_fetch -> synthesize -> cite -> final`

Агент не должен завершаться после первичного списка поисковых кандидатов,
оставлять visible todo незакрытым или выдавать progress-only final.

## What Changed

- Добавлен `research_depth` contract:
  `none | light_search | source_verified_report`.
- Для `source_verified_report` runtime требует достаточное evidence:
  search, successful fetches, source diversity and final/source coverage.
- `web_search` теперь считается candidate discovery, а `web_fetch` -
  verification layer for report-like work.
- Tool observations from web are wrapped as untrusted external data for the
  model while structured JSON remains available to runtime/UI.
- Trace summary exposes research diagnostics:
  depth, search/fetch counts, domains, source links, missing fetched evidence,
  incomplete visible todos and unknown tools.
- Failed/blocked fetches no longer count as successful evidence.
- Repeated fetch failures allow an explicit best-effort fallback instead of an
  infinite loop.
- Force-final paths no longer bypass unfinished visible todos.
- A meaningful final synthesis/output answer can close final deliverable todos
  such as `summary`, `output`, `вывод`, `ответ`.
- Forced final reminders now include concrete fetched URLs so the model can
  cite the right sources.
- Chat demo source shelf persists normalized `SourceEvidence` from runtime
  events and reloaded sessions.

## Verified Scenarios

Live checks on 2026-05-31 with `qwen/qwen3-235b-a22b-2507`:

- `research-report-requires-fetch` passed as `run_55c60d563aaf`.
- `research-compare-frameworks` passed as `run_372686378fee`.

Live regression on 2026-05-31 with `openai/gpt-5.5` via OpenRouter:

- `research-report-requires-fetch` failed as `run_f125336d0b6c`.
- Trace verdict: `fail`; terminal event: `run_failed`; provider rejected one
  request.
- Tool path: `todo_write`, then 6 `web_search` calls, 0 `web_fetch` calls.
- Research state: `source_verified_report`, `fetch_count=0`, required
  `fetch_count>=2`, final readiness `repair_needed`.
- Failure flags: `run_failed_or_cancelled`, `search_only_research_report`.
- Artifact location: `/tmp/chat-demo-live/latest-failed` and
  `/tmp/chat-demo-live/research-report-requires-fetch`.

Final acceptance on 2026-05-31 with `openai/gpt-5.5` via OpenRouter:

- `research-report-requires-fetch` passed as `run_657ce790e764`.
- Tool path included 6 `web_search` calls and 3 successful `web_fetch` calls.
- Source diversity passed with 2 domains: `en.wikipedia.org`, `dl.acm.org`.
- Trace verdict: `pass`; terminal event: `run_completed`.
- Research final readiness: `allowed`; no failure flags.

Adjacent model acceptance before the GPT-5.5 gate:

- `anthropic/claude-sonnet-4.6`: `run_a456373785f7`, pass, 6 search / 4 fetch
  / 2 domains.
- `qwen/qwen3.7-max`: `run_6c135c4f0682`, pass after forced-tool catalog
  narrowing, 10 search / 11 fetch / 4 domains.
- `deepseek/deepseek-v4-flash`: `run_d5961935104d`, pass, 8 search / 6 fetch
  / 3 domains.
- `z-ai/glm-4.7`: `run_f5baa71005e8`, pass, 2 search / 4 fetch / 2 domains.
- `moonshotai/kimi-k2.6`: `run_20600e4625ba`, pass, 14 search / 15 fetch /
  4 domains.

Focused checks run for the final slice:

```bash
.uv-bootstrap/bin/uv run pytest \
  tests/runtime/test_research_session_contract.py \
  tests/runtime/test_tool_stage_protocol.py \
  tests/runtime/test_streaming_runner.py \
  examples/chat-demo/backend/tests/test_run_trace_summary.py -q

.uv-bootstrap/bin/uv run black --check ...
git diff --check
```

Final broad checks after GPT-5.5 acceptance:

```bash
.uv-bootstrap/bin/uv run pytest tests/llm tests/runtime tests/observability -q
.uv-bootstrap/bin/uv run pytest examples/chat-demo/backend/tests -q --import-mode=importlib
cd examples/chat-demo/frontend && npx pnpm test --run
CHAT_DEMO_URL=http://localhost:5174 \
  .uv-bootstrap/bin/python examples/chat-demo/frontend/tests/e2e/chat_concepts_smoke.py
```

## Recurring Practice

For future research/provider changes, use the shared verification loop in
[Testing](testing.md) and [Chat demo](chat-demo.md):

1. Run deterministic tests for the touched contract/guard.
2. Run the relevant live chat probe with `CHAT_DEMO_LIVE_MODEL`.
3. Inspect `/trace-summary` and Phoenix for tool order, fetch success, final
   answer/source shelf, terminal event and absence of progress-only final.
4. Store failed artifacts under `/tmp/chat-demo-live*` and turn repeated trace
   failures into small runtime contracts before adding orchestration.

## Follow-Up Track

Provider/model capability, bounded repair, unknown-tool recovery and provider
failure semantics are tracked in
[Research Provider Quality Architecture Plan](archive/2026-05/research-provider-quality-architecture-plan-2026-05-31.md).
Operational provider/model debugging notes and the OpenRouter live model matrix
are tracked in [Provider and model debugging](provider-model-debugging.md).
