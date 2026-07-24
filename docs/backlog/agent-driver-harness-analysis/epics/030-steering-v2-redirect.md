# Steering v2: полный диспетчер, redirect-семантика, единый cancel

Дата создания: 2026-07-23 (исследование референсов, раунд 2). Статус: **DONE (A-E)** 2026-07-24.

## Итог (2026-07-24)

- **A. Диспетчер достроен** (`runtime/control/dispatcher.py`): INTERRUPT → `abort_handle.abort`
  (мостик в единый cancel-путь), CANCEL_QUEUED_MESSAGE → `store.cancel`, GET_CONTEXT_USAGE →
  journal-событие token-давления, PATCH_PLANNING_STATE → merge planning-state. Не-wired kind
  (SET_MAX_THINKING_TOKENS / STOP·CONTINUE_SUBAGENT) → `control_kind_unsupported` WARNING +
  mark FAILED — НЕ молчаливый `False`, item не висит QUEUED. Сигналы `control_kind_unsupported`
  / `control_payload_invalid` / `context_usage_report` в реестр 025 (lock-тест).
- **B. Redirect** (opt-in, инертно без probe): `ControlKind.REDIRECT_USER_MESSAGE` +
  `RunnerConfig.redirect_probe`. `completion.py::_await_with_redirect` рейсит provider.complete
  против probe; при поправке ОТМЕНЯЕТ только текущий LLM-запрос (не тулы/детей) и бросает
  `RedirectRequested`; `llm_step::_apply_redirect_correction` кладёт assistant-чекпоинт (роль-
  альтернация) + поправку настоящим user-ходом + `request_only_context` рамку (026), re-request;
  бюджет 2 redirect/шаг; на границе шага REDIRECT деградирует до enqueue (hermes «degrade to
  steer в тул-фазе»). Сигнал `steering_redirect_applied`.
- **C. Leftover-протокол**: недренированные NEXT/LATER команды в терминал-метадате
  (`leftover_controls`, raw-free preview) → хост доставит следующим ходом (раньше хвост очереди
  висел до следующего рана).
- **D. Хост**: `redirect_probe` = замыкание над Redis command-queue (кросс-контейнерно API↔
  jobworker, consume-once mark_applied); `POST /chat_v2/{id}/redirect` enqueue-ит REDIRECT-
  команду; композер два действия «Перебить» (redirect) / «Уточнить» (enqueue) + редактируемые
  чипы очереди (`onDelete` → cancelChatV2Command для queued).
- **E. Приёмка**: 6 движковых тестов (unsupported-signal, redirect abort+re-ask end-to-end,
  inert-без-probe, leftover); свод зелёный (2 pre-existing phase6-фейла); хостовые route-тесты
  65/65; steering-smoke расширен redirect-уровнем; live/Playwright на no-GPU обходе.

## Не в скоупе

Clarify-плоскость (вопрос ОТ агента — horizon №9), turn lease (№10).

Мотивация: инвентаризация показала, что стиринг-канал работает, но наполовину: диспетчер
применяет **4 из 11** объявленных `ControlKind` (`runtime/control/dispatcher.py:54-92` —
SET_MODEL, SET_PERMISSION_MODE, SET_TOOL_POLICY, ENQUEUE_USER_MESSAGE); `interrupt` через
стиринг — мёртвая буква (отмена живёт параллельным путём `/chat_v2/{id}/cancel` +
cancellation_probe), `cancel_queued_message` в контрактах есть, но у хоста «Очередь
уточнений» уже с чипами. Уточнение сейчас применяется только на границе шага — на
20-60-секундном LLM-вызове пользователь ждёт весь вызов.

## Reference-first

- **hermes `run_agent.py` steer/redirect** — двухуровневая коррекция: `steer(text)` —
  мягкая (батчится с \n, НЕ прерывает, вливается в последний tool-result на границе);
  **`redirect(text)`** — жёсткая: отменяет ТОЛЬКО текущий LLM-запрос (не тулы, не детей),
  сохраняет завершённые сообщения, частичный reasoning записывает как plain-контекст,
  коррекцию добавляет НАСТОЯЩИМ user-ходом и перезапрашивает; во время тул-фазы
  деградирует до steer. Leftover-steer возвращается хосту в result → следующий ход
  (сообщение не теряется).
- **openclaude `messageQueueManager` + `query.ts:2773`** — приоритетная очередь
  (now > next > later), **редактируемость до отправки** (`popAllEditable`), агент-скоупинг
  (промпты пользователя не утекают субагентам); **mid-turn инъекция**: на границе
  тул-итераций очередь вливается attachment-сообщениями В ТЕКУЩИЙ ход — без
  cancel-restart и без слома кэш-префикса.
- Interruption-correction (eb72c77) уже воспроизведён у нас каналом request_only_context
  (026) — redirect должен использовать его для рамки «предыдущий ход прерван — это поправка».

## Эскиз фаз

A. **Достроить диспетчер**: wired-статус всем объявленным kind'ам — INTERRUPT (мостик в
   cancellation_probe → единый cancel-путь, хостовый `/cancel` становится тонким шимом),
   CANCEL_QUEUED_MESSAGE, PATCH_PLANNING_STATE, SET_MAX_THINKING_TOKENS,
   GET_CONTEXT_USAGE (ответ в журнал), STOP/CONTINUE_SUBAGENT (при активном subagents).
   Не-wired kind → явный WARNING signal `control_kind_unsupported` (в реестр 025), не
   молчаливый False.
B. **Redirect**: новый kind REDIRECT_USER_MESSAGE — при живом LLM-вызове abort текущего
   запроса (сохранение частичного вывода как plain-контекст, tombstone-дисциплина 016/018),
   коррекция настоящим user-ходом + рамка через request_only_context; во время тул-фазы —
   деградация до enqueue. Бюджет: не более 1 redirect на шаг (анти-шторм).
C. **Leftover-протокол**: недренированные steer-сообщения при финализации возвращаются
   в терминальном payload → хост перекладывает в следующий ход (сейчас хвост очереди
   после финала повисает до следующего рана).
D. **Хост UX**: композер — два действия («Уточнить» = enqueue, «Перебить» = redirect);
   чипы очереди редактируемы до применения; ярлыки статуса из 025.
E. Приёмка: живой сценарий Playwright — уточнение во время 30-секундного вызова
   применяется без ожидания конца вызова (redirect) и без потери начатого тул-прогресса;
   стиринг-smoke `run_chat_v2_steering_live_smoke.py` расширить обоими уровнями.

## Не в скоупе

Clarify-плоскость (вопрос ОТ агента — horizon №9), turn lease (№10).
