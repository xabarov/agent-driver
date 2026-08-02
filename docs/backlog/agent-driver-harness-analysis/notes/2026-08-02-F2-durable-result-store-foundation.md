# F2 — Durable result/output store foundation

Дата: 2026-08-02. Статус: **design-note (кода нет)**. Разблокирует: **U3 prior-result full-replay**
(эпик 051); достраивает `ApprovalConsumptionStore`. Связано: [[051-atomic-approval-resume]],
upstream-requirements.md.

## Зачем

U3 DoD: «a concurrent duplicate with the same idempotency key **returns the prior recorded result**
and never re-executes the tool». Сейчас (после U3 B/C/D) CAS-ledger отклоняет дубль
`ResumeConflictError` до второго исполнения тула — **exactly-once уже гарантирован**, — но **прежний
результат не возвращается verbatim**: `ApprovalConsumptionStore` хранит только строку `result_ref`,
не полный `AgentRunOutput`. F2 наполняет и читает результат.

## Текущее состояние (grounded)

| Что | Где | Факт |
|---|---|---|
| `ApprovalConsumptionStore` | `runtime/control/approval_store.py` | in-mem + SQLite; строка `result_ref` в записи + метод `record_result(run_id, interrupt_id, result_ref)`; колонка `result_ref` в таблице `approval_consumptions` |
| Consume-путь | `resume.py::_handle_resume_with_pending` | `try_consume` до исполнения (CAS); `record_result` **пока не вызывается** — result_ref всегда None |
| Дубль-исход | `ConsumeOutcome(status=DUPLICATE, prior_result_ref=...)` | возвращает ref, но вызывающий его не превращает в output |
| Терминал рана | `AgentRunOutput` (`contracts/runtime.py`), строится `_build_output` | JSON-сериализуем, round-trippable |

## Design decision

**Персистить терминальный `AgentRunOutput` (JSON) по ключу консьюма; на дубль возвращать его
verbatim вместо повторного вождения рана.**

Решения к фиксации ДО кода:
1. **Отдельный стор vs расширение approval-store.** Предлагаю **расширить `record_result`** до
   приёма полного output-JSON (переименовать семантику `result_ref` → `result_payload` или добавить
   колонку `result_payload TEXT`). Минус отдельного стора — дубль ключа (run_id,interrupt_id) уже
   первичный в approval-ledger; логично держать результат рядом с consume-записью.
2. **Когда писать.** После терминального `_build_output` в резюмированном ране — единый сайт в
   `runner.run()` (рядом с `_finalize_abort_lifecycle`), если consume прошёл и есть approval-store.
3. **Размер.** `AgentRunOutput` может быть большим (длинный answer/трейсы). Политика: писать
   **компактную проекцию** (status, answer, terminal_reason, run_id) ИЛИ полный dump с усечением
   answer по лимиту (переиспользовать `ensure_bounded_json_metadata`-подход из U2). Решить: хосту
   для идемпотентного HTTP-ретрая обычно достаточно (status, answer, terminal_reason) — начать с
   компактной проекции, расширять по требованию.
4. **Кто превращает ref→output.** Точка, где сейчас поднимается `ResumeConflictError("already
   consumed")` (`_resolve_resume_checkpoint` / consume-gate) — при наличии записанного результата
   вернуть его как терминальный output вместо исключения (опционально, за флагом
   `replay_prior_result`, чтобы не менять текущую конфликт-семантику по умолчанию).

## Код-сайты (additive)

- `runtime/control/approval_store.py` — колонка/поле `result_payload`; `record_result` пишет JSON;
  `get`/`try_consume` возвращают его в `ConsumeOutcome`.
- `runtime/runner.py::run` — после терминала: `approval_store.record_result(...)` компактной
  проекцией output.
- `resume.py` — опц. ветка replay: при DUPLICATE с записанным результатом вернуть его (за флагом).
- контракт `ConsumeOutcome` — уже несёт `prior_result_ref`; добавить `prior_result_payload`.

## Тест-план

- Approve → терминал записан в стор; дубль-approve с `replay_prior_result` → возвращает прежний
  output verbatim, тул НЕ исполнен (счётчик == 1).
- Crash-after-consume-before-record: запись результата отсутствует → дубль по-прежнему даёт стабильный
  conflict (не переисполняет) — регресс exactly-once не допускается.
- SQLite durability: новый инстанс стора видит записанный результат (restart-replay).
- Backward-compat: без флага `replay_prior_result` — прежняя `ResumeConflictError`-семантика.

## Риски / митигидии

- **Размер payload** — компактная проекция по умолчанию; bounded-валидатор.
- **Рассинхрон consume↔result** (записали consume, не записали result из-за краха) — это НОРМА:
  дубль остаётся безопасным (conflict, без переисполнения); replay просто не доступен до записи.
- **Двойная семантика конфликта** (raise vs replay) — за явным флагом, default — текущее поведение.

## Не в скоупе

Персист промежуточных (не терминальных) состояний; полный event-replay (это durable-checkpoint,
отдельно).
