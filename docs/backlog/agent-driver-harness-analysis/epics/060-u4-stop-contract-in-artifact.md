# R4 — U4 Stop-контракт целиком в релизном артефакте

Статус: **pending** (после 057 — переиспользует trace). Верифицирует эпик 052 (U4).
Контекст: `../REMEDIATION_PLAN.md` §060.

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
