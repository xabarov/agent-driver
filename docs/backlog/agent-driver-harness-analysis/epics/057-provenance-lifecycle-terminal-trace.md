# R1 — ToolGate provenance: full lifecycle (terminal + trace + adversarial matrix)

Статус: **pending**. Доводит эпик 050 (U2 `IN PROGRESS`). Контекст: `../REMEDIATION_PLAN.md` §057.

## Что уже есть (не переделываем)

`tool_call_id`/`attempt_id` в `ToolGateContext`; `GateProvenance(decision_id, policy_snapshot_id,
metadata)` → `_ad_gate_provenance` (reserved `_ad_`); `ensure_bounded_json_metadata` fail-closed;
проводка в approval-interrupt + envelope.

## Незакрытые gaps

Terminal-outcome projection + trace/support projection + полная adversarial-матрица (handoff §7).

## Фазы

- A. **Terminal projection** — provenance в terminal-outcome/`RuntimeDecision`; gate-DENY ≠ static-DENY;
  evidence-receipt несёт `tool_call_id`+`decision_id`.
- B. **Trace projection (redaction-safe)** — provenance в trace без утечки host-секретов; redaction-тест.
- C. **Identity-инварианты** — `tool_call_id` стабилен gate→terminal; `attempt_id` меняется только на
  новой исполнительной попытке (не на retry той же операции).
- D. **Adversarial-матрица** — allow/deny/ask+resume/retry/failure/timeout/abort; malformed/oversized/
  non-JSON/reserved-key metadata → детерминированный fail-closed; model/tool не может создать/перезаписать
  host-provenance.

## Acceptance (1:1 с R1)

Стабильный `tool_call_id` gate→terminal; `attempt_id` только на новой попытке; provenance в
checkpoint+events+envelopes+traces+terminal; host-provenance неперезаписываема из model/tool; fail-closed
матрица; покрытие allow/deny/ask/retry/failure/timeout/abort + redaction-safe trace; нет
contradictory identity и required skip/xfail.

## Не в скоупе

PentestLens-семантика provenance-полей (только generic host-metadata).
