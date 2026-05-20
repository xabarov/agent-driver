# CLI self-test matrix

Reproducible interactive checks for agent-driver chat behavior across models.

## Run locally

```bash
uv run python tools/selftest/run.py \
  --scenarios A,B,C,D \
  --matrix m1=qwen/qwen3-235b-a22b-2507,m2=openai/gpt-4o-mini
```

Offline smoke (fake provider):

```bash
uv run python tools/selftest/run.py --provider fake --matrix m1=fake --scenarios B --smoke-only
```

`--smoke-only` checks only harness health (`exit_code`, no traceback). Use full scenario rubric with a live provider.

## Scorecard columns

| Column | Meaning |
| --- | --- |
| product | Scenario behavior checks (tools used, URLs, doctor signal, etc.) |
| infra | Process health (`exit_code_zero`, no traceback in log) |
| provider_error | Transport/provider failure taxonomy when present |
| failed | Combined gate used for harness exit code |

## Exit code

- `0` when all cells pass product + infra and no `provider_error` (unless `--allow-provider-errors`).
- `1` when any cell fails.

## Scenarios

- **A** — Fresh external knowledge (web_search + web_fetch + URL, no stale SAM markers)
- **B** — Top-level markdown listing via glob_search
- **C** — file_write denial + `/doctor`
- **D** — Repo env flags via grep_search (`AGENT_DRIVER_WEB_SEARCH_BACKEND`)

## Live provider instability

Use `--allow-provider-errors` for exploratory runs when OpenRouter is flaky. Treat `provider_error` column as infra signal, not product regression.

## Debug LLM 400 payloads

```bash
AGENT_DRIVER_DEBUG_LLM_PAYLOAD=1 uv run agent-driver chat --plain ...
```

Logs redacted request stats (role char counts, tool_call ids) on provider rejection.
