# F1 — Attempt-epoch / result fencing foundation

Дата: 2026-08-02. Статус: **design-note (кода нет)**. Разблокирует: **U4 fencing +
`LATE_RESULT_IGNORED`** (эпик 052), помогает U4 mid-LLM-await abort. Связано:
[[052-durable-stop-host-cancellation]], upstream-requirements.md.

## Зачем

U4 DoD требует: «late handler completion cannot reopen the run, schedule further actions, or
overwrite the terminal cancellation record». Сейчас такого guard'а **нет вообще** (инспекция
2026-08-02): результаты тула не сверяются с попыткой рана, `attempt_id` используется только для
корреляции событий/спанов. Handler, вернувшийся после того как ран уже терминировал (cancel/timeout),
не отбрасывается по identity — держится лишь на `RunStatus`/`step_name=="done"`, не на durable
cross-process-эпохе.

## Текущее состояние (grounded)

| Что | Где | Факт |
|---|---|---|
| `attempt_id` | `runtime/single_agent/types.py:448-450` (property), `:494` (поле event/span) | только event/span-корреляция (`run_id:attempt_id`); с результатами тула НЕ сверяется |
| Attribution результата | `tools/executor/allowed.py:231` (`await handler(args)`), envelope-метадата | результат не несёт epoch/generation попытки |
| Cancellation | `runtime/abort.py` (`is_aborted`), чек на границах шагов | кооперативная, poll-based; нет fence на возвращённый результат |
| «fence» в дереве | `continuation.py:58-63`, `finalization/output.py:624` | это markdown code-fence, НЕ run-epoch (не путать) |
| `TerminalReason` | `contracts/enums/runtime.py:20-34` | 12 членов + добавленный `CANCELLATION_FAILED`; `LATE_RESULT_IGNORED` отсутствует (коллизии нет) |

## Design decision

**Ввести монотонный per-run `attempt_epoch: int`, который несут tool-результаты; терминал/сборка
результата отбрасывает результат с устаревшим epoch.**

Решения к фиксации ДО кода:
1. **Где живёт epoch.** На `RunContext` (рядом с `attempt_id`), инкрементится при старте новой
   попытки/резюме. Начальное значение 0; resume, порождающий новую исполнительную попытку, → +1.
2. **Как результат несёт epoch.** Прокинуть текущий epoch в tool-invocation (тем же contextvar-
   идиомом, что `tool_cancellation_scope` из U4-hook-среза) и штамповать в envelope-метадату
   результата (reserved-ключ `_ad_attempt_epoch`, namespace из U2).
3. **Где guard.** В точке складывания результата в контекст/терминал: если
   `result.epoch != context.attempt_epoch` → **drop + событие** `RESULT_FENCED` (не терминал), а
   для терминального late-случая — `TerminalReason.LATE_RESULT_IGNORED` в наблюдаемости (не меняет
   уже-записанный терминал).
4. **Late-vs-terminal.** Поздний результат НЕ переоткрывает ран (это ядро). Вопрос только в
   наблюдаемости: событие-дроп vs terminal-reason. Предлагаю **оба**: событие всегда, terminal-reason
   только если это был бы единственный исход.

## Код-сайты (additive)

- `runtime/single_agent/types.py` — `attempt_epoch` на `RunContext` (+ инкремент в resume-пути).
- `tools/context.py` — `tool_attempt_epoch_scope` / `current_tool_attempt_epoch()` (по образцу
  `tool_cancellation_scope`).
- `tools/executor/allowed.py:~231` — штамп epoch в envelope-метадату вокруг вызова handler'а.
- складывание результата (`tool_stage`/`finalization`) — epoch-guard + `RESULT_FENCED`-событие.
- `contracts/enums/runtime.py` — `LATE_RESULT_IGNORED`.
- `contracts/enums/events.py` (или где `RuntimeEventType`) — `RESULT_FENCED`.

## Тест-план

- Handler, возвращающий результат ПОСЛЕ терминала (cancel) → assert результат отброшен, ран не
  переоткрыт, `RESULT_FENCED`-событие есть.
- Два attempt-epoch: результат старой попытки приходит после resume новой → отброшен по epoch.
- Backward-compat: без нового epoch-механизма (default 0, никто не инкрементит) — поведение
  идентично (все результаты той же попытки проходят).

## Риски / митигидии

- **Contextvar-протечка epoch между тасками** — использовать тот же scoping-паттерн, что уже
  проверен для `tool_cancellation`/`tool_progress` (reset в finally).
- **Ложные дропы** — начинать с observe-режима (событие, без изменения исхода), включать enforce
  только под флагом/после инцидент-подтверждения (правило promotion из ledger).

## Не в скоупе

Форсибельная отмена in-flight handler'а (это U4 mid-LLM/cooperative-cancel, отдельно) — F1 только
про **атрибуцию и отбрасывание** результата, не про прерывание.
