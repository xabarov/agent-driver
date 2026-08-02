# R4 — U4 Stop-контракт целиком в релизном артефакте

Статус: **DONE** (верификация + добор матрицы). Верифицирует эпик 052 (U4).
Контекст: `../REMEDIATION_PLAN.md` §060.

## Код в артефакте (R4 point a) — ПОДТВЕРЖДЕНО

Весь U4-набор — предок `remediation-0.3.0` (проверено `git merge-base --is-ancestor`): deadline-wiring
`d43720d` **и** namespace `7bf1c6d` — **в ветке**. Release 0.3.0 (эпик 061) режется из этой ветки →
wheel содержит весь U4-код (в отличие от `0.2.0`, где deadline был post-cut).

## Матрица R4 (cell → покрывающий тест)

| Ячейка acceptance | Тест |
|---|---|
| token identity (run/call/attempt) + bounded deadline | `test_tool_cancellation.py::test_cancellation_token_carries_bounded_deadline`, `::test_handler_sees_cancellation_token_with_identity` |
| abort during LLM await | `test_mid_llm_abort.py` |
| cooperative-handler cancellation | `test_tool_cancellation.py::test_handler_wait_cancelled_returns_on_mid_flight_abort` |
| uncooperative-handler → CANCELLATION_FAILED | `test_cancellation_failed.py` |
| completion-race / result fencing | `test_result_fencing_enforce.py` |
| abort during approval-wait | `test_abort_resume_interaction.py` (resume с уже-aborted handle) |
| abort before/at planning (no new transition) | `test_u4_stop_matrix.py::test_abort_before_planning_cancels_without_plan_interrupt` |
| no-new-work после observed abort | `test_tool_cancellation.py::test_already_aborted_skips_new_work` + `test_runner_abort.py` |
| durable lifecycle + restart readback | `test_runner_abort_lifecycle.py`, `test_abort_lifecycle_store.py::test_sqlite_survives_new_instance` |
| terminal: cancelled | `test_runner_abort_lifecycle.py::test_aborted_run_records_observed_and_cancelled` |
| terminal: completed-before-cancel | `test_runner_abort_lifecycle.py::test_completed_before_cancel_is_recorded` |
| terminal: cancellation-failed | `test_cancellation_failed.py::test_abort_during_stuck_step_is_cancellation_failed` |
| late-result-ignored (fenced, не воскрешает ран) | `test_u4_stop_matrix.py::test_fenced_late_result_does_not_reopen_cancelled_run` + `test_result_fencing_enforce.py` |

**Примечание:** `LATE_RESULT_IGNORED` (`TerminalReason`) определён, но late-result игнорируется через
fencing-механизм (событие `RESULT_FENCED`, straggler дропается и НЕ переоткрывает ран), а не как terminal —
это доказано выше. Терминальный вариант enum зарезервирован, но текущий контракт покрывает поведение
«ignore late result» на уровне события; это осознанно и задокументировано (не required-gap).

## Что уже есть (052, помечен DONE)

Durable abort lifecycle, result fencing, `CANCELLATION_FAILED`, mid-LLM abort, bounded cancellation
deadline (`d43720d`).

## Незакрытые gaps

1. Весь U4-код должен попасть в **release-SHA** (deadline `d43720d` — post-cut, не в wheel `0.2.0`).
2. Полнота матрицы + terminal/late-result-различение.
3. (при необходимости) trace-проекция U4-исходов — переиспользует 057.

## Фазы

- A. **Полнота матрицы** — abort во время planning / approval-wait / LLM-await / cooperative-handler /
  uncooperative-handler / completion-race / process-restart; все ветки реально покрыты (не xfail).
- B. **Terminal-различение** — cancelled / completed-before-cancel / cancellation-failed /
  late-result-ignored различимы; после observed-abort **не** начинается ни один network/tool action.
- C. **Identity+deadline в токене** — host cancellation-token несёт run/call/attempt-identity + конечный
  deadline (из budget).
- D. **В артефакт** — весь U4-набор (incl. deadline wiring) в release-source-commit (координируется с 061).

## Acceptance (1:1 с R4)

Wheel из commit со всем U4; матрица покрыта; terminal различает 4 исхода; no-new-action после abort;
token с identity+deadline.

## Не в скоупе

Durable Gateway (Option 2 остаётся допустимым).
