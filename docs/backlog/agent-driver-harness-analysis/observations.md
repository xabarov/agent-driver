# Cross-Cutting Observations

Date: 2026-07-03

## Source Availability

The IDE referenced `excel_ai/docs/backlog/agent-driver-harness-analysis/...`,
but this checkout only contains `docs/` under `agent-driver`; the original 008
epic and checklist files were not present in the working tree, `git ls-files`,
or local git history. The backlog records in this directory were reconstructed
from the code and tests that are present.

## Host Portability

Capability-pack adapter manifests are part of the release interface for host
adoption. They should not encode a contributor's local checkout paths. Use
relative commands from `AGENT_DRIVER_REPO` and adapter-owned env vars for sibling
projects or interpreters. This keeps dry-run output shareable and makes
deterministic runs repeatable on CI and host machines.

## Gate Semantics

Do not treat a skipped optional gate as release evidence. A skipped gate needs a
reason that says why no claim is being made. Required gates should remain
`not_run`, `blocked`, or `failed` until evidence exists.

For deterministic capability-pack runs, the persisted validation-artifact
manifest is acceptable evidence for `support_bundle_artifact` in the inert seed
slice. Live releases may still require a full runtime support bundle plus
policy-supervision audit.

## Artifact Shape

`validation_gates.json` should use the same shape everywhere:

- `count`
- `statuses`
- `gates`
- `redaction`

Raw `{"gates": [...]}` files are harder for support tooling and host release
scripts to consume consistently.

## Test Drift Around Budget Limits

Some tests still expected endless tool-call loops to fail with
`tool_policy_denied`. The current runtime contract is more nuanced: tool loops
are bounded, but the default path can finish through a forced/final-answer
terminal before exceeding the hard `tool_calls > max_tool_calls` failure check.
Tests that need a pure failed cap should explicitly configure
`RunnerConfig(budget_grace_enabled=False)` and use a scenario that actually
exceeds the hard limit.

## Metadata Inventory Discipline

The runtime metadata inventory is part of the validation surface. Streaming and
provider fallback diagnostics such as `assistant_stream_events_seen`,
`assistant_stream_token_chunks_seen`, `last_provider_diagnostics`, and
`provider_stream_non_stream_fallback` must be documented when literal
`context.metadata` keys are introduced.

## Continuous Validation Semantics

The continuous-validation layer should remain an offline evidence aggregator
first. `agent-driver capability-pack audit` consumes persisted
`evidence_index.json` and `manifest.json` rows, verifies sizes/checksums, and
writes validation JSON/Markdown reports. It must not infer live/provider/UI or
benchmark success from deterministic artifacts. In `--no-live` mode, unexecuted
OpenRouter, Phoenix, Playwright and benchmark gates should become `no_claim` or
`stale`, not `passed`.

## Repository Inventory Limits

Large, unbounded repository scans can fail before they provide evidence. During
the 2026-07-03 continuous-validation implementation, broad parallel scans over
`.git`, virtualenvs, caches and generated reports hit "Too many open files".
Future evidence inventory should target known artifact roots first and exclude
generated directories before treating scan errors as missing evidence.

## Provider Catalog Status Vocabulary

Provider compatibility contracts and validation gates intentionally use
different status vocabularies. Provider facts use `supported`, `degraded`,
`blocked`, `cache_hit` and `no_claim`; validation gates use `passed`, `failed`,
`blocked`, `skipped`, `stale` and `no_claim`. New provider-catalog reports must
translate between those vocabularies at artifact boundaries instead of reusing
provider fact statuses as gate statuses.

## Provider Artifact Type Wiring

When adding a new provider evidence artifact type, wire it through all artifact
contracts together: `EvidenceArtifactRef`, `ValidationArtifactRef`,
continuous-validation known artifact mapping, and gate-id mapping. Updating only
one layer produces artifacts that write successfully but fail strict 008 audit.


## Multi-Turn Anaphora to the Assistant's Own Prior Answer

> **STATUS 2026-07-15: ADDRESSED** in commit 7d6dc3df — `ContextBudget.protect_recent_turns` (default 4) keeps the recent tail (both roles) so assistant-enumerated antecedents survive trimming. 12 tests pass. Root cause was the deterministic trimmer keeping an oldest-first prefix, not a missing condensation step.

Date: 2026-07-15 (host: MeetScript Ask Meetings, chat_v2 on agent-driver)

Observed engine-level gap: the harness resolves anaphora that points at earlier
**user** turns (query condensation / standalone-question rewrite), but does NOT
resolve a reference to a list the **assistant itself** enumerated in a previous
turn. Repro: assistant answers "15 meetings are about AI: <enumerated list>";
user follows up "how many of those 15 are about NLP?"; the assistant fails to
bind "those 15" to its own prior answer, sees only the fresh retrieval (6 hits),
and asks a clarifying question ("which list of 15 do you mean?") instead of
filtering the already-established set.

Root cause is in the context/condensation plane, not the domain tools or infra:
the previous assistant message's enumerated entities are not carried into the
model's resolvable context as an antecedent. The fix belongs in the harness
(steering/condensation), so it benefits every host, not just MeetScript — this
is a general engine bug and may land directly on `main`. Classify precisely from
a Phoenix trace first (confirm what the model actually saw in context), then
extend anaphora resolution to cover assistant-turn antecedents (not only prior
user turns). Related host-side tracking: MeetScript epic backlog B17 (and the
chat-quality cluster B15/B16/B17).

**Reference-first (general over point-fix):** before implementing, study how the
conversation-history/context class is handled in `reference/openclaude`
(`src/history.ts`, `src/QueryEngine.ts`, `src/context/`) and `reference/hermes-agent`.
Apply the most general antecedent-resolution mechanism the engine can host, so
every consumer benefits — do not point-patch MeetScript.

## Persistent Empty Final Completion After a Tool Cycle (deepseek-v4-flash)

> **STATUS 2026-07-18: ADDRESSED** in commit 86a4424 — third force-final strategy
> `request_with_folded_tool_history` (tool exchanges folded into plain user/assistant
> turns, evidence preserved) + terminal signal `forced_final_empty_after_all_retries`
> (severity=error) so hosts can message the user honestly. 2 new tests.

Date: 2026-07-18 (host: MeetScript Ask Meetings, chat_v2, synthetic «Аргус» benchmark)

Observed: on some prompts that route through a tool call (find_meetings), the
follow-up FINAL completion from deepseek-v4-flash returns empty content ("\n\n",
finish_reason=stop). The engine's existing recovery already fires — warnings
`provider_empty_forced_final_non_stream_retry` (retry w/o streaming) and
`provider_forced_final_tool_call_no_tools_retry` (retry w/ tools disabled) — but
both retries ALSO return empty, and the run terminates `completed` with
`answer: ""`. The user sees an empty chat bubble. Flaky per prompt-shape: the
same case sometimes yields a full answer (first benchmark run passed it).

Same model-level quirk class as MeetScript speaker-rename `json_schema` empties
(there the fix was switching to the tools-mode extractor). Model returns empty
for certain trailing-history shapes; the retries keep the SAME message history,
so they inherit whatever shape triggers the empty output.

Candidate general fix (engine, benefits every host): a third force-final
strategy that REWRITES the trailing history before retrying — fold the tool
call/result pair into a plain user-visible digest message ("Результаты
инструмента: …") and ask for the final answer with a clean tail. Also consider
a terminal guard: a `completed` run whose final answer is empty after all
retries should surface a distinct terminal reason (not a silent empty answer),
so hosts can message the user honestly. Reference-first: check how
`reference/openclaude` / `reference/hermes-agent` normalize trailing tool
history for models with this quirk before implementing.

## Hidden 1-Tool-Call Budget When Hosts Omit max_tool_calls / max_steps

> **STATUS 2026-07-18: ADDRESSED** — `_force_final_reason` resolves budgets as
> per-run → runner default stamped into context metadata → documented backstops
> (`DEFAULT_MAX_TOOL_CALLS_BACKSTOP=32` added alongside max-steps 80); journal
> terminal check gains the symmetric `default_max_tool_calls` fallback. Full
> runtime suite green (no test relied on the 1-call fallback), 4 new tests.

Date: 2026-07-18 (host: MeetScript chat_v2, «Аргус» benchmark, decisions_log case)

`_force_final_reason` (tool_stage) falls back to `context.metadata.get("max_tool_calls", 1)`
/ `metadata.get("max_steps", 1)` when `AgentRunInput` leaves the budgets None — and nothing
in the single-agent runtime ever writes those metadata keys. Net effect: a host that does
not explicitly pass budgets gets an agent forced to finalize after its FIRST tool call.
`RunnerConfig.default_max_steps` (backstop 80) is never consulted by `_force_final_reason`,
so the documented backstop philosophy and the actual forced-final behavior disagree.
Observed live: cross-meeting questions finalize with «мне нужно найти остальные отчёты…»
or an empty forced final (model considers the task unfinished after one search).

Candidate engine fix: make the fallback consult the runner-level default budgets
(default_max_steps and a new default_max_tool_calls) instead of the hardcoded 1, keeping
explicit per-run budgets winning. Behavior change for all hosts → decide deliberately,
with tests over profiles that relied on single-shot tools. Host-side mitigation applied in
MeetScript meanwhile (explicit max_tool_calls=6 / max_steps=12 via env).
