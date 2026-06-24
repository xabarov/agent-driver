# CLAUDE.md — agent-driver

Working guide for this repo. Short and high-signal — read every session.

## What this is

`agent-driver` is the domain-neutral runtime/SDK that powers the agent in the sibling
**excel-ai** product (`../excel_ai`). It is evolved from this same workspace: SDK bugs
discovered while running excel-ai get fixed here and logged in `CHANGELOG.md`
(`[Unreleased]`).

## Design principle (shared with excel-ai)

System quality = **strong model** + **right powerful tools** + **right harness**. This repo
*is* the harness layer: steering, planning, context summarization, durable execution, tool
governance. Improve via these levers — do **not** benchmark-fit or micro-tune for specific
cases. Keep this runtime domain-neutral; domain-specific guards belong in the consumer
(excel-ai), not here.

## Boundary

- Genuine runtime bugs → fix here, log in `CHANGELOG.md`.
- Domain guards (chart-promise, no-loss-guard, markdown-strip, …) → stay in excel-ai.

## Running tests (uv is NOT on PATH — `make test` won't work)

- `.venv/bin/python -m pytest <path>` (the `.venv` python has project deps installed).
- Async tests use **pytest-asyncio** in STRICT mode → need `@pytest.mark.asyncio`.
- Default `addopts` excludes `live` and `slow`; no `pytest-timeout` → bound with shell `timeout`.

## Gotchas paid for in time

- **No default step cap.** `_terminal_from_limits`
  (`agent_driver/runtime/single_agent/lifecycle/journal.py`) only honors caller-supplied
  `max_steps` / `max_tool_calls` / `deadline_seconds` / `cost_budget_usd` — all default `None`.
  If a run never reaches `final_answer` (e.g. an executor that always raises), the step loop
  runs forever and `journal._next_seq` is O(n) per emit → RAM climbs into GBs and it looks
  like a hang. Any test/caller that may not reach final_answer MUST set `max_steps`
  (`max_steps=2` allows one LLM step + the tool/code step, then terminates with the trace
  recorded).
