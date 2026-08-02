# R3 — Plan policy binding через checkpoint/resume/trace

Статус: **pending**. Доводит эпик 053 (U5 `IN PROGRESS`). Контекст: `../REMEDIATION_PLAN.md` §059.

## Что уже есть (не переделываем)

Harness-authored `content_hash` (+ EDIT re-hash); `detect_plan_revision`;
`PlanningPolicyInput.required_plan_hash` (gate DENY на ревизии); opaque host policy-binding; durable
`PlanArtifactStore` на approve/reject.

## Незакрытые gaps

Binding+identity через **trace projection** (handoff §7); доказать re-approval через реальный путь.

## Фазы

- A. **Persist через checkpoint/resume** — `plan_id`+`content_hash`+`revision`+host policy-binding
  переживают checkpoint persistence и resume (не helper/in-mem dict).
- B. **Trace projection (redaction-safe)** — binding в runtime/trace-проекции execution-journal.
- C. **Overwrite-guard** — попытки перезаписи binding/hash из model/tool payload отклоняются/игнорируются.
- D. **Re-approval on EDIT** — материальная ревизия меняет authoritative hash и до tool-execution требует
  нового approval по host policy.

## Acceptance (1:1 с R3)

Binding+identity переживают checkpoint+resume; присутствуют в redaction-safe trace; overwrite отклоняется;
EDIT→новый hash→re-approval до execution; тест через реальный checkpoint/resume/trace путь, не helper.

## Не в скоупе

PentestLens authorization/policy-семантика (binding остаётся opaque host-blob).
