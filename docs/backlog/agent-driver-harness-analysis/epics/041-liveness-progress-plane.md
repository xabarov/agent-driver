# Liveness/progress plane: idle-bounded side-LLM calls

Дата создания: 2026-07-30 (horizon-scan 040, кандидат №2 — конвергенция обоих
референсов). Статус: **DONE (A-D)** 2026-07-30.

> Реализация: **A** `llm/liveness.py` — `bounded_side_completion` + `AuxIdleTimeout`
> (idle=None → голый complete; иначе стрим + ре-агрегация текста, per-chunk idle через
> `wait_for`, total-потолок `max(600, 4×idle)`, фолбэк на complete без стриминга). **B**
> `CapabilitySettings.aux_idle_timeout_seconds` (default None, threads через flat kwargs +
> делегирующее свойство); `aux_completion` идёт через `bounded_side_completion`. **C**
> `run_full_llm_compaction` принимает idle-таймаут; зависший провайдер → `AuxIdleTimeout` →
> graceful `success=False` (failure_kind `aux_idle_timeout` → outcome
> `llm_compaction_aux_idle_timeout`), циркуит-брейкер бонит повторы. **D** 12 тестов; полный
> свип **2802 passed**; snapshot полей CapabilitySettings обновлён; CHANGELOG + ledger.
> Отложено: (c) subagent stall по progress-token — крупный parent↔child плюминг, смежно 034/045.

Мотивация: главный цикл защищён `LlmStreamIdleTimeout` (стрим idle-таймаут) +
`stage_wait_heartbeat` (эпик 025). Но **side/aux-вызовы провайдера не защищены ничем** —
зависший провайдер на компакции / extraction / suggestions / грейдере блокирует ран
навсегда:

- `context/compaction/llm_full.py:70` — LLM-компакция (реальный консюмер каждой компакции),
- `llm/structured.py:164` — structured-output extraction,
- `llm/suggestions.py:252` — suggested-next-questions (эпик 038),
- `llm/aux.py:151` — aux-субстрат (эпик 032).

## Reference-first

Оба референса в одну неделю независимо инвестировали в liveness-таймауты вместо
wall-clock (главный сигнал скана 040):

- **hermes `32fd9d65c`/`99a381f31`/`5f5afb1ee`**: thread-local `aux_progress_hook` в
  `auxiliary_client` — первичный `call_llm` форсится в `stream=True`, чанки
  ре-агрегируются в полный ответ, таймаут действует **per-read (idle)**, а не как total;
  degenerate-trickle стрим ограничен потолком `max(600s, 4× task_timeout)`. Мотивация:
  30s wall-clock дедлайн hygiene-компакции убивал медленные но здоровые summary-модели →
  300s cooldown оставлял сессию oversized → doom loop. hermes `cfb206fe2` (wall-clock
  watchdog) был **отреверчен** — выжил именно idle/progress-подход.
- **openclaude `c23b6e1`**: heartbeat-обёртка тулов (30s synthetic progress), покрывающая
  всю длительность вызова.

## Что у нас уже есть (не переделываем)

- **(a) Heartbeat-обёртка стадий** — `stage_wait_heartbeat` (эпик 025) оборачивает
  LLM-completion и tool-stage: `signal_id=stage_wait_heartbeat`, info-severity, повтор
  каждые `interval`. Молчаливая стадия отличима от мёртвой.
- **Стрим-idle главного цикла** — `LlmStreamIdleTimeout` (эпик 018/016) на главном
  провайдерском стриме.

## Незакрытый gap (этот эпик)

**(b) idle-bounded side-вызовы.** Общий доменно-нейтральный помощник: side-`complete`
идёт стримом с per-chunk idle-таймером + total-потолком; зависший вызов даёт **честный
bounded fail** (`AuxIdleTimeout`), а не вечный hang. Медленный-но-здоровый стрим
выживает (idle сбрасывается каждым чанком). Провайдер без стриминга → фолбэк на
`complete` с total-`wait_for`. Default off (idle=None) → текущее поведение без изменений.

## Фазы

A. **`llm/liveness.py`** — `bounded_side_completion(provider, request, *,
   idle_timeout_seconds, total_ceiling_seconds=None)` + `AuxIdleTimeout`. idle=None →
   голый `complete` (ноль оверхеда). Иначе: стрим + ре-агрегация текста (side-вызовы
   text-only, без тулов), per-chunk idle через `wait_for(anext, idle)`, total-потолок
   `max(total_ceiling or 600, 4× idle)`. Фолбэк на `complete`+total при отсутствии
   стриминга. Юниты.
B. **Конфиг + aux.** `CapabilitySettings.aux_idle_timeout_seconds: float | None = None`.
   `aux_completion` принимает idle-таймаут → идёт через `bounded_side_completion`.
C. **Компакция.** `run_full_llm_compaction` принимает idle-таймаут; зависший
   compaction-провайдер → `AuxIdleTimeout` → компакция возвращает success=False
   (циркуит-брейкер уже бонит повторы), не вешает ран. Liveness-сигнал
   `signal_id=aux_stage_idle_timeout`.
D. **Приёмка.** Юниты (idle-stall raises; медленный здоровый стрим выживает;
   idle=None не меняет поведения; фолбэк без стриминга), полный свип, CHANGELOG,
   статус эпика, ledger.

## Не в скоупе (осознанно отложено)

- **(c) Subagent stall по progress-token** (hermes `99a381f31`: sampler
  `api_call_count + current_tool + last_activity_ts`, grace-период, терминальный
  `stalled`-ивент). Отдельный крупный кусок: требует progress-плюминга от детей к
  родителю; у нас сейчас wall-clock `deadline_seconds`. Отложено — вернуться после (b),
  смежно 034/045.
- **Streaming re-aggregation тулов** в side-вызовах — side-вызовы text-only по контракту,
  тулы там не нужны.
