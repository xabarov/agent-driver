# Test Plan and Coverage Matrix

This plan defines how we test new features in `agent-driver`, with an explicit matrix for runtime, tools, planning/context, and live traces.

## Test Plan (Execution Order)

For any non-trivial feature (new tool, planning/context changes, runtime semantics):

1. Run targeted deterministic tests for touched modules.
2. Run full offline suite (`PYTHONPATH=. .venv/bin/pytest`).
3. Run required live smoke checks (`-m live`) for provider and agent+tool lanes.
4. Review traces/events and summarize findings before merge.

Reference policy: [Testing and live trace policy](testing-and-live-trace-policy.md).

## Coverage Matrix

| Area | What to verify | Test type | Current evidence | Gap policy |
| --- | --- | --- | --- | --- |
| Tool contracts | Manifest/schema validity, profile compatibility | Unit/contracts | `tests/contracts/test_tools_contracts.py` | Add contract tests on any schema/risk changes |
| Governed executor | `allow/deny/interrupt`, budgets, metadata | Unit/runtime | `tests/runtime/test_tool_governance_executor.py`, `tests/runtime/test_tool_governance_hitl.py` | Add decision-path regression on policy changes |
| Filesystem tools | `read_file`, `glob_search`, `grep_search`, `file_write`, `file_edit`, `notebook_edit` happy+guards | Unit/tools | `tests/tools/test_builtin_filesystem_tools.py` | Add error/limit/ignore/path-glob and deterministic edit/write contract tests for each change |
| Web tools | `web_fetch`, `web_search` parsing, safety, truncation | Unit/tools | `tests/tools/test_builtin_web_tools.py` | Add content-type/url/timeout/parser fallback tests on behavior changes |
| CodeAgent loop | parse/execute/retry/final-answer semantics | Unit/runtime+eval | `tests/runtime/test_code_agent_profile.py`, `tests/runtime/test_code_agent_multistep.py`, `tests/evals/test_code_agent_evals.py` | Add regression test for every loop semantic fix |
| Planning/context | planning state, trimming, microcompaction invariants | Unit/context | `tests/context/test_runtime_phase6_metadata.py`, `tests/context/test_deterministic_trimming.py`, `tests/context/test_microcompaction.py` | Add invariant tests for ordering/provenance metadata changes |
| Context quality | fact retention after trimming/microcompaction/compaction | Unit/eval/live | Planned: `tests/context/test_context_quality_eval.py`, `tests/runtime/test_live_context_quality_openrouter.py` | Required for Phase 8 compaction changes; compare quality/cost/latency before changing defaults |
| Replay/observability | replay view, eval comparison, trace export | Unit/evals | `tests/evals/test_replay_views.py`, `tests/evals/test_persisted_replay.py`, `tests/observability/test_trace_export.py` | Add assertions on new metadata fields |
| Live providers | real network health + completion | Live | `tests/llm/test_live_providers.py` | Required for adapter/provider changes |
| Live agent+tool | real provider + governed tool stage (`web_search` mock lane + real `bash`/`notebook_edit`/`file_write`/`file_edit` allow lanes + interrupt lanes for `bash` and `file_write`) | Live | `tests/runtime/test_live_agent_tool_smoke.py` | Required for tool/runtime integration changes; verify both allow-path side effects and interrupt-path HITL payload/trace semantics |

### Shell Tool Safety Contract

For `bash` changes, keep deterministic safety tests mandatory before any live lane:

- allowlisted read-only command executes and returns bounded output;
- destructive keyword/prefix and redirection patterns are blocked;
- timeout path kills long command and marks `timed_out=true`;
- governed executor interrupts when run policy requires approval for `medium+` risk.
- live lane (`-m live`) executes one real allowlisted `bash` command via agent+tool and validates both structured output and trace status.

## Context Quality Matrix

The Phase 6 context stack is deterministic context hygiene, not yet semantic
summarization. These tests measure whether future Phase 8 compaction work
actually preserves useful facts instead of only reducing prompt size.

| Lane | Fixture | Provider | What to Measure | Pass Gate | Planned Evidence |
| --- | --- | --- | --- | --- | --- |
| Offline retention baseline | Synthetic long session with needle facts, tool observations, planning updates, artifacts/digests | Fake provider / deterministic runner | Which required facts survive in prompt/projection/audit after aggressive budgets | `fact_recall >= 0.80`, no orphan observation/tool ids, audit explains all dropped facts | `tests/context/test_context_quality_eval.py` |
| Offline replay baseline | Same fixture rendered through full debug, succinct, and CLI replay views | No provider | Whether planning events and context decisions are inspectable without raw metadata digging | Replay exposes planning channel, token pressure, trim audit, microcompaction audit | `tests/evals/test_context_quality_replay.py` |
| Live OpenRouter recall | Same needle-fact fixture, aggressive trim/microcompact, real OpenRouter-compatible completion | OpenRouter via `OpenAICompatibleProvider` | Model can answer a strict JSON recall prompt from surviving context | JSON parses; `remembered` covers required facts above threshold; `missing` is bounded and traceable | `tests/runtime/test_live_context_quality_openrouter.py` |
| Strategy comparison | Baseline variants: trim-only, trim+microcompact, trim+digest/session memory, full LLM compact | Fake + optional OpenRouter | Quality/cost/latency tradeoff per strategy | Report contains recall, hallucinated facts, prompt tokens/cost, latency, metadata completeness | `agent_driver/evals/context_compaction_runner.py`, `tests/evals/test_context_compaction_runner.py` |

### Context Quality Metrics

- `fact_recall`: required facts recovered by the model or retained in deterministic projection.
- `hallucinated_facts`: facts returned by the model that are not present in the fixture.
- `provenance_coverage`: remembered facts that point to a source observation, digest, artifact, planning event, or prompt message.
- `audit_completeness`: every dropped/compacted context block has a trim or microcompaction record.
- `budget_efficiency`: retained facts per estimated prompt token.
- `latency_ms` and `provider_cost`: recorded for live/provider-backed lanes.

### OpenRouter Live Lane Contract

Live context-quality tests are opt-in and must stay skipped by default:

```bash
AGENT_DRIVER_RUN_LIVE_TESTS=1 \
AGENT_DRIVER_OPENAI_BASE_URL=https://openrouter.ai/api/v1 \
AGENT_DRIVER_OPENAI_API_KEY=... \
AGENT_DRIVER_OPENAI_MODEL=... \
.venv/bin/python -m pytest -m live tests/runtime/test_live_context_quality_openrouter.py
```

The live test should ask the model to return strict JSON, for example:

```json
{
  "remembered": ["fact id or exact fact"],
  "missing": ["fact id or exact fact"],
  "confidence": 0.0
}
```

Assertions should prefer structured fact ids over free-form text matching. When a
fact is missing, the test should also verify whether it was actually removed by
trim/microcompaction or whether the model failed to use retained context.

## Context Quality Work Plan

1. Add `tests/context/test_context_quality_eval.py` with a deterministic fixture
   of user facts, tool observations, stdout/stderr, planning updates, artifacts,
   and digests.
2. Add a small scoring helper under `agent_driver/evals/` for recall,
   hallucination, provenance coverage, and budget efficiency.
3. Add replay assertions so planning and context decisions can be reviewed from
   support bundles and CLI replay.
4. Add `tests/runtime/test_live_context_quality_openrouter.py` behind the
   existing `AGENT_DRIVER_RUN_LIVE_TESTS=1` gate.
5. Use the same fixture to compare Phase 8 strategies before enabling any
   semantic compaction by default.

## Minimal Merge Checklist

- Targeted tests for touched area are green.
- Full offline test suite is green.
- Required live lane(s) are green for tool/runtime/context changes.
- Context-quality baseline is updated when trimming, microcompaction, session
  memory, or compaction behavior changes.
- Trace review summary is recorded in PR notes (event ordering, tool statuses, terminal reason, key metadata).
