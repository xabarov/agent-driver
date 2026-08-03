# Release handoff — agent-driver 0.3.3 (Zion context integrity)

Date: 2026-08-03. This handoff pins the `0.3.3` artifact identity requested by the
Zion downstream (Chat, Recon Copilot, multi-agent Reporter). It resolves the
upstream remediation request recorded in
[`docs/backlog/zion-context-integrity-remediation.md`](./../zion-context-integrity-remediation.md)
— the six constrained-context integrity regressions and the public run-scoped
budget seam — landed via merged PR #1 (`Fix/zion context integrity`) plus PR #2
(`keep run event loop live during SSE`).

## Release identity

- Package version: `0.3.3`.
- Release source commit:
  `dd262e32b341607b3ecd4d2d0f4f3c69e2db81ec`.
- Public remote: `https://github.com/xabarov/agent-driver`.
- Release source status: clean (`git status --porcelain` returned no output).
- Supported Python: `>=3.11`; CI exercises Python 3.11 and 3.12.
- Handoff content commit: recorded separately when this document is committed;
  it is deliberately distinct from the release source commit above.

The release source contains the complete `0.3.2` reproducible-artifact and
durable control-plane baseline plus the `0.3.3` context-integrity work:

- leading system contracts and the current non-system turn survive
  message-count trimming;
- `compaction_protected` / `compaction_evidence` (with non-empty
  `material_unit_hashes`) messages reach compaction intact, with a deterministic
  size-only over-budget audit reason (no silent unbounded growth);
- structured JSON tool results are replaced by an unambiguous typed stub, never a
  raw slice; structured tool arguments stay atomic (no string-marker type change);
- honest token pressure counts message metadata/tool calls and the tool schema
  catalogue, and the public context breakdown uses the same accounting;
- the supported `RunContextBudget` field on `AgentRunInput` with the
  `resolve_run_context_budget` facade and a bounded full-compaction packet, plus a
  one-release deprecation window for `app_metadata.context_budget`;
- `serialize_runtime_state_for_compatibility(..., target="0.2.0rc5")` for rolling
  rollback, whose audit records paths/strategies only — never raw messages,
  evidence, tool payloads, or reasoning;
- the LLM completion loop additionally blind-retries transient
  connection/transport errors (`ConnectError` / `RemoteProtocolError` /
  `ReadError` / `ProviderTransportError`) up to twice with bounded backoff.

## Exact wheel

- Filename: `agent_driver-0.3.3-py3-none-any.whl`.
- Size: `1193787` bytes.
- SHA-256:
  `62e3a929f9ca48007248f45d27b8440d95fbd6214c768dc6d761c7aaca93c56d`.
- `SOURCE_DATE_EPOCH`: `1785766075` (the release commit timestamp).
- Supported release builder: CPython `3.12.3`, `setuptools==83.0.0`,
  `wheel==0.47.0`.
- METADATA: `Name: agent-driver`, `Version: 0.3.3`,
  `Requires-Python: >=3.11`, `License-Expression: LicenseRef-NOASSERTION`.

`make release-wheel` exports the exact clean Git commit, normalizes tracked file
modes, fixes locale/timezone/hash seed/umask and refuses an unpinned build
toolchain. Two local builds under caller umasks `077` and `002` produced the same
SHA-256 above. GitHub Actions repeats the same two-build byte comparison and
uploads artifact
`agent-driver-wheel-dd262e32b341607b3ecd4d2d0f4f3c69e2db81ec`.

## Verification results

- GitHub Actions run
  `https://github.com/xabarov/agent-driver/actions/runs/30821943363`:
  **success** on the exact release source commit.
- All eight mandatory jobs passed: test 3.11, test 3.12, lint 3.11, lint 3.12,
  type, docs, real-Postgres suite, and reproducible release-wheel (job
  `release-wheel` = success; artifact retained 30 days).
- Local full default suite on the exact release source:
  **3028 passed, 70 deselected, 6 xfailed** in 65.88 seconds. The deselected
  groups are the repository's explicit live/slow/Postgres opt-ins; the mandatory
  real-Postgres matrix ran in its separate successful CI job.
- The five portable Zion context-integrity contracts (system/evidence retention,
  no raw-slicing of structured current-turn/tool-result content, atomic
  structured tool arguments, honest token pressure incl. tool schemas) and the
  two run-budget adoption contracts pass on the release source; the default
  no-budget request path is byte-identical to `0.3.2`.
- The exact wheel imports `agent_driver`, `agent_driver.embedding`,
  `agent_driver.contracts`, and `agent_driver.runtime` successfully after
  installing its declared runtime dependencies (CI install-smoke).

## Consumer pin for Zion

```text
Agent Driver release version: 0.3.3
Release source commit SHA: dd262e32b341607b3ecd4d2d0f4f3c69e2db81ec
Wheel filename: agent_driver-0.3.3-py3-none-any.whl
Wheel SHA-256: 62e3a929f9ca48007248f45d27b8440d95fbd6214c768dc6d761c7aaca93c56d
Wheel size: 1193787 bytes
SOURCE_DATE_EPOCH: 1785766075
Public remote: github.com/xabarov/agent-driver
CI: GitHub Actions run 30821943363 = success (8/8 jobs)
CI artifact: agent-driver-wheel-dd262e32b341607b3ecd4d2d0f4f3c69e2db81ec
Build toolchain: CPython 3.12.3 / setuptools 83.0.0 / wheel 0.47.0
```

Zion must pin the release source commit above, not a later documentation commit
or floating `main`.

## Residual limits

- The supported type gate covers the public embedding/control-plane/context-budget
  surface, not every internal module.
- The `app_metadata.context_budget={input_tokens, output_tokens}` legacy input is
  honored for one deprecation window; new callers use `RunContextBudget`.
- The rollback serializer down-converts additive checkpoint/resume fields to the
  `0.2.0rc5` shape; the host still owns removing any field unknown to an even
  older rollback target.
