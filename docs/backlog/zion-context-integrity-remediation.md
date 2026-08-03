# Upstream remediation request — preserve embedder context integrity after 0.3.2

Suggested issue title:

> Preserve system/evidence/structured payload integrity and expose run-scoped context budgets

Canonical target repository: `https://github.com/xabarov/agent-driver`

Reproduced on: `0.3.2`, source `39d9736fc4eeaedbc1c3b4c61e8d33907f3e2ba5`

Downstream: Zion (Chat, Recon Copilot, multi-agent Reporter)

Severity: release-blocking data/contract integrity regression; no security claim

## Problem

Stock 0.3.2 contains the durable control-plane features Zion needs, but it is not
a semantic superset of Zion's current rc5 embedding. Under constrained context,
the public release can:

1. discard the leading system contract when enforcing `max_messages`;
2. trim material evidence before the compaction stage can summarize it;
3. raw-slice a structured tool result into a misleading, malformed fragment;
4. omit tool-call metadata and the tool schema catalogue from token pressure;
5. replace a large structured tool argument with a string marker, changing its
   type and invalidating replay;
6. ignore the run-scoped input/output budget formerly supplied through
   `AgentRunInput.app_metadata.context_budget`, including the bounded compaction
   packet derived from it.

This is observable as incomplete or validator-like reports in a strong-model
workflow: the model receives a damaged contract/evidence packet before it writes
operator prose. A host-side report sanitizer cannot reconstruct evidence that
the runtime already discarded.

## Reproduction

The downstream executable contract is:

```bash
pytest -q tests/agent_runtime/test_agent_driver_context_budget_adoption.py
```

On stock 0.3.2, five portable tests fail:

- `test_message_cap_preserves_system_contract_and_current_turn`;
- `test_material_evidence_reaches_compaction_before_route_budget_trimming`;
- `test_structured_current_turn_and_tool_result_are_never_raw_sliced`;
- `test_pressure_counts_tool_call_metadata_and_tool_catalog`;
- `test_large_structured_tool_argument_stays_atomic`.

The two prior run-budget tests cannot be executed through a supported 0.3.2
seam because `_effective_context_budget` and `_effective_compaction_char_cap`
were private. The requested fix is **not** to re-export those private functions;
it is to provide a public, typed replacement and preserve their semantics.

The focused 0.3.2 result is `5 failed, 1 passed`; the passing cell is the bounded
OpenRouter 402 retry. Two separate rc5/0.3.2 persisted-state compatibility
fixtures pass when Zion down-converts new additive checkpoint fields during the
rollback window.

## Required behavior

### A. Message and evidence retention

- Leading system messages and the current non-system turn survive message-count
  trimming.
- Messages tagged `metadata.compaction_protected=true` survive route trimming.
- A message tagged `metadata.compaction_evidence=true` with non-empty
  `material_unit_hashes` reaches compaction intact, even when the immediate
  route character budget is lower than the packet.
- The over-budget retention is explicit in a deterministic audit reason; it is
  not silent unlimited growth.

### B. Structured data remains structurally valid

- Never raw-slice JSON current-turn content or JSON tool-result content.
- A shortened tool result must be valid JSON produced by structure-aware leaf
  shrinking, or an explicit typed/stub representation that cannot be confused
  with the original payload.
- Do not replace a JSON/object tool argument with a plain string marker. Keep it
  atomically when protected/recent, or use a typed spill/reference contract.
- Emit an audit entry recording retained/spilled/truncated strategy and original
  size without embedding sensitive raw content.

### C. Honest token pressure

- Count message content, message metadata/tool calls, and tool definitions.
- `TokenPressureInput` (or its supported successor) accepts tool schemas and
  reports at least `prompt_metadata_chars`, `tool_schema_chars`, and
  `tool_schema_count`.
- The total used by the compaction trigger and the public context-breakdown API
  must agree.

### D. Public run-scoped budget seam

Provide a typed supported input rather than requiring an embedder to patch
single-agent internals. It must cover:

- input-token window and output-token reserve;
- message/observation semantic caps;
- recent-message and preview retention;
- maximum compaction packet size;
- deterministic source/audit metadata.

Backward compatibility may continue to read
`app_metadata.context_budget={input_tokens, output_tokens}` for a deprecation
window. For the Zion fixture `180000/30000`, the rc5 semantic result was:

```text
max_chars=720000
max_messages=360
max_observations=360
microcompact_preserve_recent=90
microcompact_max_preview_chars=2700
context_window_estimate=210000
full_compaction_max_chars=60000
```

Equivalent bounded semantics are acceptable; exact private helper names are not.

## Persisted-state / rolling rollback requirement

0.3.2 adds checkpoint revision/resume fields that strict rc5 models cannot read.
A rolling release must either:

- provide a documented compatibility serializer/down-conversion hook; or
- document the additive schema boundary and support a host wrapper that removes
  only fields unknown to the rollback version.

Acceptance includes both directions: rc5 writes → new release reads, and new
release compatibility writer → rc5 reads. No product database reset is allowed.

## Proposed implementation ownership

- Agent Driver owns generic trimming, structure preservation, token-pressure
  accounting, the typed budget seam, and compatibility serialization hook.
- Zion owns report meaning, evidence selection, tenant/scope policy, UI and its
  chosen numerical budget policy.
- Do not add Zion-specific report vocabulary or a Zion package dependency.

## Definition of done for the upstream patch release

1. All portable tests above pass as upstream tests using public imports.
2. Existing 0.3.2 stop/fencing/resume/duplicate-approval tests remain green.
3. New audit metadata contains sizes/strategies but no raw secret, chain of
   thought, or complete evidence payload.
4. Public docs include the typed run-budget and rollback-serialization examples.
5. A patch release is cut from an exact clean commit with a reproducible wheel.
6. The release contains an explicit SPDX licence declaration and a repository
   licence file, or the publisher documents why package metadata is
   `NOASSERTION`.

## Downstream acceptance after release

Zion will pin the exact source/wheel, rerun E50-01→E50-03, then replay E44/E45
privacy/report fixtures before a paid canary. Until those gates pass, rc5 remains
the production runtime.

All implementation work, review and release cutting happen in this GitHub
repository. A downstream GitLab remote is not a development fork and will only
mirror accepted GitHub refs after release.
