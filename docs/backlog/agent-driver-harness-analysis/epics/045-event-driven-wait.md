# Event-driven wait: park-on-event вместо поллинга

Дата создания: 2026-07-30 (horizon-scan 040, добавлен по запросу пользователя).
Статус: **DONE (A-D)** 2026-07-30.

> Реализация: **A** `contracts/wait_for_event.py` (`WaitForEventRequest/Resolution/Status`,
> `clamp_wait_deadline` — подписка всегда ограничена: default 3600, ceiling 86400) +
> `InterruptReason.WAIT_FOR_EVENT`; ре-экспорт из `contracts`. **B** builtin-тул
> `wait_for_event` (`tools/planning.py`) + `_append_wait_for_event_interrupt` в
> `executor/allowed.py` (зеркалит clarification-interrupt, allowed_actions CLARIFY/CANCEL) →
> ран паузится с подпиской в paused-output (переиспользует всю pause-машинерию). **C**
> `wait_for_event_resolution_from_resume` (CLARIFY→delivered payload / CANCEL→cancelled /
> `WAIT_TIMEOUT_STATE_KEY`→timed_out) + `wait_for_event_timed_out`-классификатор дедлайна.
> **D** 17 тестов (вкл. сквозной pause→deliver(CLARIFY)→resume→complete); полный свип
> **2838 passed**. Промпт-правило «долгое внешнее ожидание → подписка, не поллинг» встроено в
> описание тула (модель его видит). Уточнение к скетчу: timeout сигналится хостом через
> `WAIT_TIMEOUT_STATE_KEY` в resume-state_patch (хост владеет часами и event-source'ом), не
> движковым сигналом — движок доменно-нейтрален.

Мотивация: модель, которой велено «дождись окончания внешнего процесса», сегодня вынуждена
tool-поллить статус (и per-turn капы 019 её за это накажут, а поллинг ре-читает контекст —
дорого по токенам). Нужен штатный примитив: подписка на внешнее событие → checkpoint →
освобождение петли → wake с payload'ом события. Бонус: припаркованный ран не мутирует
префикс — дружит с prompt-cache (028).

## Что уже есть (переиспользуем, не строим заново)

Полная pause/resume-машинерия через interrupts: тул возвращает структурный вывод →
исполнитель конвертирует в `InterruptRequest` + `ToolPolicyDecision.INTERRUPT` → ран
паузится (`_build_paused_output` генеричен по reason, несёт `result.interrupt`) →
`ResumeCommand` будит ран. `CLARIFY`-resume уже кладёт payload как user-turn и ставит
`next_step=llm_call`; `CANCEL` отменяет. Checkpoint/resume + durable `PAUSED`-статус готовы.

## Незакрытый gap (этот эпик — доменно-нейтральный движковый примитив)

Нет первоклассного «жду внешнее событие»-reason'а и типизированной подписки. Park-on-event
= новый `InterruptReason.WAIT_FOR_EVENT` + контракт подписки, поднимаемый тулом
`wait_for_event`, с **liveness-бэкстопом** (подписка ВСЕГДА ограничена дедлайном — не может
висеть вечно; тай-ин к 041).

## Фазы

A. **Контракты** `contracts/wait_for_event.py`: `WaitForEventRequest`
   (`event_key`, `deadline_seconds`, `poll_fallback_seconds?`, `description`),
   `WaitForEventResolution` (`status: delivered|timed_out|cancelled`, `payload`),
   `WaitForEventStatus`. `InterruptReason.WAIT_FOR_EVENT`. Liveness: `deadline_seconds`
   всегда ограничен (default 3600, clamp ≤ 86400) — подписка не может быть бесконечной. Юниты.
B. **Примитив** `wait_for_event` builtin-тул + `_append_wait_for_event_interrupt` в
   исполнителе (allowed_actions `[CLARIFY, CANCEL]`, `expires`-из-deadline в metadata) →
   ран паузится с подпиской в paused-output. Runner-тест паузы.
C. **Резолюция + liveness** — `wait_for_event_resolution_from_resume(resume)` (delivered
   payload из `CLARIFY.message` / cancelled из `CANCEL`) + `wait_for_event_timed_out(...)`
   классификатор дедлайна и сигнал `wait_for_event_timed_out` (подписка, не выстрелившая к
   дедлайну, деградирует в timeout, НЕ висит — бэкстоп 041). Юниты + deliver/timeout-тесты.
D. **Приёмка** — полный свип, промпт-правило «долгое внешнее ожидание → подписка, не
   поллинг» (документ), CHANGELOG, статус, ledger, регистрация сигнала.

## Не в скоупе (осознанно)

- **Реальные event-source'ы** (exit процесса, inotify, webhook, очередь) и доставка resume —
  на стороне хоста: движок даёт подписку в paused-output, хост подписывается и шлёт
  `ResumeCommand`. Движок доменно-нейтрален.
- **Crash-safe delivery-claim** через рестарт (чертёж hermes `async_delegation`, отложенный
  №14) — следующий слой; сейчас liveness-дедлайн даёт корректность без вечного зависания.
- Продуктовая примерка — MeetScript jobworker (notify вместо поллинга статуса job'а).
