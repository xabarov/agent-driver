# Статус-протокол для хостов (эпик 025)

Контракт: **у пользователя никогда не должно быть немой длинной стадии** — если ран жив,
последнее событие журнала честно описывает, что происходит. Хост-UI вправе показывать
подпись ПОСЛЕДНЕГО события (паттерн MeetScript); протокол гарантирует, что при любой
стадии дольше `stage_heartbeat_seconds` (дефолт 10s) появится новое событие.

Референсы: openclaude «Never render nothing» (`SystemAPIErrorMessage.tsx`: «a silent
retry is indistinguishable from a hang»), hermes `_emit_wait_notice` + gateway-heartbeat.

## Событийный словарь (RuntimeEventType) — семантика для UI

Полный enum — `agent_driver/contracts/enums/runtime.py`. Ключевые для статус-строки:

| Событие | Семантика | Рекомендация UI |
|---|---|---|
| `run_started` / `run_queued` / `run_resumed` | жизненный цикл рана | «Готовлю ответ» |
| `llm_call_started` / `llm_call_completed` | вызов модели начат/завершён | «Отправляю запрос к модели» / «Модель вернула черновик» |
| `assistant_message_started` + `token_delta` / `reasoning_delta` | стриминг ответа | показывать текст/счётчик токенов сразу (openclaude 01a01fb: с первого токена, без порога) |
| `assistant_message_completed` | финальный текст готов | текст показан; далее возможны финальные проверки |
| `assistant_message_replaced` / `_tombstoned` | ответ заменён ретраем лестницы / отозван | заменить пузырь; причина в `replacement_reason` |
| `tool_call_started` / `tool_progress` / `tool_call_completed` | инструмент | «<имя инструмента>…»; длинный тул без progress прикрыт heartbeat'ом |
| `lifecycle_hook_started` / `_completed` / `_timed_out` | финальные проверки (эпик 024); payload `{hook, phase, duration_ms|timeout_seconds, requested_revision}` | hook=`rubric` → «Проверяю полноту ответа»; `_timed_out` → «проверка не уложилась в бюджет — завершаю» |
| `memory_compaction_started` / `memory_compacted` | компактизация контекста | «Сжимаю контекст» (hermes: явные start/done маркеры) |
| `warning` | см. реестр signal_id ниже | ярлык по signal_id; неизвестный retry-сигнал → generic «Повторяю запрос к модели» |
| `runtime_decision` | решение полиси (force_final и т.п.) | не для статус-строки; для трейс-разбора |
| `checkpoint_saved` | прогресс сохранён | НЕ использовать как долгоживущий ярлык — событие мгновенное («Сохраняю прогресс»-инцидент 2026-07-22) |
| `run_completed` / `run_failed` / `run_cancelled` | терминал | разблокировать ввод |

## Реестр warning `signal_id`

Тест-замок `tests/runtime/test_status_protocol_registry.py`: каждый signal_id в коде
обязан быть в этой таблице. Класс: **transient** — попытка внутри рана, показывать
живым статусом; **durable** — состояние изменилось/исчерпано, показывать заметно и
после успеха (hermes: durable-смены эмитятся даже при успешном восстановлении).

| signal_id | Класс | Что случилось | Рекомендация UI |
|---|---|---|---|
| `stage_wait_heartbeat` | transient (severity=info) | стадия `stage` всё ещё идёт, `elapsed_ms` прошло | «Всё ещё жду модель/инструмент — N s» |
| `prompt_cache_broken` | transient (severity=info) | cache_read упал >5% и >2000 токенов при НЕизменном префиксе (TTL/провайдер); эпик 028 | не для статус-строки; диагностика стоимости |
| `provider_transient_error_retry` | transient | 408/429/5xx от провайдера, bounded-ретрай через `retry_in_seconds` | «Провайдер временно недоступен — повторяю запрос» |
| `provider_empty_forced_final_non_stream_retry` | transient | пустой forced-final стрим, ретрай без стриминга | «Модель вернула пустой ответ — повторяю запрос» |
| `provider_empty_forced_final_history_fold_retry` | transient | пустые финалы, ретрай со свёрнутой историей | «Повторяю запрос (упрощаю историю)» |
| `provider_empty_forced_final_fallback_provider_retry` | durable | основной провайдер исчерпан, пробуем fallback-провайдер | «Переключаюсь на резервную модель» |
| `provider_forced_tool_choice_removed_retry` | transient | провайдер отверг forced tool_choice | generic retry |
| `provider_invalid_encrypted_reasoning_retry` | transient | провайдер отверг эхо reasoning-метаданных | generic retry |
| `provider_max_tokens_reduced_retry` | transient | 402: снижаем max_tokens и повторяем | generic retry |
| `provider_context_overflow_compact_retry` | transient | запрос не влез в окно; компакт + ретрай | «Сжимаю контекст и повторяю запрос» |
| `provider_stream_non_stream_fallback` | transient | стрим не открылся/умер до пользы; non-stream ретрай | generic retry |
| `provider_stream_partial_final_recovered` | durable | финал восстановлен из частичного стрима | пометить ответ как восстановленный (сведения о запуске) |
| `forced_final_recovered_prior_turn` | durable | финал взят из предыдущего содержательного хода | пометить ответ как восстановленный |
| `forced_final_empty_after_all_retries` | durable | вся лестница исчерпана, финал пуст | честная ошибка + «Повторить» |
| `compaction_circuit_breaker_open` | durable | компактизация отключена брейкером (cooldown) | не для статус-строки; диагностика |
| `compaction_empty_result_skipped` | transient | компакт вернул пусто, пропущен | не для статус-строки |
| `tool_failure_streak_warning` | transient | 2 фейла инструмента подряд | «Инструмент сбоит — пробую ещё раз» |
| `tool_failure_streak_force_final` | durable | 3 фейла — принудительный финал | ответ по имеющимся данным |
| `control_kind_unsupported` | transient | стиринг-команда `control_kind` не поддержана на этом run-пути (эпик 030); помечена FAILED, не молчаливый drop | «Команда не поддержана» (диагностика очереди) |
| `control_payload_invalid` | transient | у стиринг-команды невалидный payload (эпик 030); помечена FAILED | «Команда отклонена (некорректные данные)» |
| `context_usage_report` | transient (severity=info) | ответ на GET_CONTEXT_USAGE: текущее token-давление в журнал (эпик 030) | не для статус-строки; диагностика контекста |
| `steering_redirect_applied` | transient (severity=info) | жёсткий redirect прерван текущий LLM-вызов, поправка добавлена настоящим user-ходом, перезапрос (эпик 030 B) | «Учитываю вашу поправку…» |

## Политика ретрай-чаттера (фаза C)

- **Каждая попытка** — отдельное warning-событие (см. выше). Хосту решать плотность
  показа: дим-строка на каждую (openclaude, «attempt k/N») или показать только
  последнюю + heartbeat (буферизация как у hermes — на стороне хоста, движок не
  скрывает попытки).
- **Durable-события показываются всегда**, даже если восстановление удалось
  (hermes `_emit_pending_fallback_notice`): смена провайдера/модели, ответ из
  восстановления, исчерпание лестницы.
- Heartbeat (`stage_wait_heartbeat`) не заменяет ретрай-события: он о «живо и ждём»,
  они — о «что-то произошло, реагируем».

## Спан-дисциплина (фаза D)

- Основной LLM-вызов — span `llm <model>` (kind LLM) — было.
- Каждый переопределённый finalize-хук — span `lifecycle_hook <name>` (kind CHAIN);
  таймаут = span со статусом ERROR. Новое в эпике 025.
- События `chat_v2.*`-моста хоста дают спаны на каждое runtime-событие (warning/
  heartbeat включительно) — ретраи видны на трейсе без движковых спанов на ступень.
- Критерий: на трейсе прогона с грейдером и ретраем нет немых интервалов > 10s.
