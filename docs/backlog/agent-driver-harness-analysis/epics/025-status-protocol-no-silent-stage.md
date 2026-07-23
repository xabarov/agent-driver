# Статус-протокол для хостов + контракт «нет немой длинной стадии»

Дата создания: 2026-07-23 (из живого инцидента MeetScript «Сохраняю прогресс»). Статус: **DONE 2026-07-23 (A-E)**.

Мотивация из живых данных: UI MeetScript показывает подпись ПОСЛЕДНЕГО события — любая немая
стадия замораживает ярлык («Сохраняю прогресс» висел 34–139s при живом ране). Phoenix-трейс
имел «дыру» без спанов. Ретраи deepseek-лестницы шли молча. Фикс 9d46af7 добавил события
точечно (finalize-хуки), фронт MeetScript вручную замапил warning signal_id → ярлыки. Оба
референса решают это не точечными событиями, а **протоколом**: у стадии есть статус,
у долгого ожидания — heartbeat, у ретрая — обязательная видимость.

## Чего нет сейчас (gap-карта 2026-07-23)

1. Нет контракта «стадия дольше X ms обязана эмитить событие/спан» — эмиссия кусочная.
2. **Warning `signal_id` нигде не перечислены** — ~20 строковых литералов по коду
   (`provider_transient_error_retry`, `forced_final_empty_after_all_retries`,
   `compaction_circuit_breaker_open`, …); хост узнаёт о них grep-ом. Нет реестра, нет
   host-facing доки «событие → что показать пользователю».
3. Нет heartbeat-механизма для долгих ожиданий (провайдер молчит 20s — журнал молчит тоже).
4. Ретрай-чаттер не нормирован: мы показываем каждый ретрай (после фикса), hermes буферизует
   и флашит только при исчерпании — нужна осознанная политика, а не случайная.

## Reference-first

- **openclaude «Never render nothing»** — `SystemAPIErrorMessage.tsx:12`: «a silent retry is
  indistinguishable from a hang» — жёсткий инвариант: ретрай всегда рендерит минимум дим-строку
  с live-обратным отсчётом и `attempt k/N`; полный блок с 4-й попытки. Спиннер — state machine
  по stream-событиям (`streaming.ts:83-143`: requesting/thinking/responding/tool-input) плюс
  каналы `spinnerMessage` (compaction: явные подписи фаз + progress ratio) и `spinnerSuffix`
  (`<toolName> +N` для долгих тулов; `running stop hooks… done/total`). Таймер «( … 7s )»
  появляется через 5s даже без стрима (`SHOW_TIMER_AFTER_MS`).
- **openclaude 01a01fb** — счётчик токенов виден с ПЕРВОГО токена (снят гейт 30s): позиция
  «прогресс видим немедленно, не после порога».
- **hermes `_emit_wait_notice`** (`run_agent.py:953`) — переписывает статус-строку при долгом
  ожидании провайдера (докстринг называет наш симптом: «staring at a generic 'cogitating...'
  spinner with no hint»). Gateway-heartbeat `_notify_long_running` (`gateway/run.py:21863`):
  редактируемый пузырь «⏳ Working — N min {current_tool / last_activity / iteration k/max}».
- **hermes buffered-then-flushed** (`run_agent.py:977-1078`): чаттер ретраев буферизуется,
  показывается ТОЛЬКО при исчерхании всех ретраев/фолбэков; но durable-смены (переключение
  провайдера) эмитятся всегда, даже при успехе. Живость на время ожидания даёт wait-notice,
  а не спам строк.
- **hermes компакция как образец стадии**: явные маркеры start/done
  (`COMPACTION_STATUS_MARKER`/`COMPACTION_DONE_STATUS`, `conversation_compression.py:48-108`),
  TUI перекрашивает в «Summarizing…» — транскрипт не «немо сбрасывается».
- **hermes `gateway/stream_events.py`** — типизированный словарь транспортных событий
  (`LongToolHint`, `Commentary`, `MessageStop(final)`, единый sink): отсутствие case — ошибка
  типов, а не молчание.

## Эскиз фаз

A. **Реестр сигналов**: `docs/status-protocol.md` — полный словарь `RuntimeEventType` +
   перечисление всех warning `signal_id` с рекомендуемой семантикой для UI (что за фаза, каким
   ярлыком показывать, транзиентно или durable). Тест-замок: каждый `signal_id` в коде обязан
   быть в реестре (по образцу `test_runtime_metadata_inventory`).
B. **Wait-notice/heartbeat**: событие `stage_heartbeat` (или переиспользовать WARNING c
   `signal_id=long_wait`) от llm_step/tool_stage, если ожидание > N s (дефолт 10s), с
   `elapsed_ms` и описанием стадии; повторяется с интервалом. Хосту достаточно показывать
   последнее — ярлык перестаёт врать по построению.
C. **Политика ретрай-чаттера**: durable-события (смена провайдера/модели, исчерпание лестницы)
   — всегда; попытки — событие на каждую (хост сам решает, дим-строка или буфер); контракт
   зафиксировать в доке из фазы A.
D. **Спан-дисциплина**: OpenInference-спан вокруг каждого finalize-хука и каждого ретрая
   лестницы (сейчас — только llm основной петли); критерий приёмки — на трейсе прогона с
   грейдером и ретраем НЕТ немых интервалов > N s.
E. Приёмка глазами оператора (Принцип №2 MeetScript): живой прогон с искусственно замедленным
   провайдером — ярлык меняется, heartbeat виден, «вечных» подписей нет.

## Не в скоупе

Содержимое ретрай-лестниц (016) и терминальная дисциплина (024) — здесь только их видимость.

## Реализация 2026-07-23 (фазы A-D)

- **A** `docs/status-protocol.md`: событийный словарь с UI-семантикой + реестр всех
  17 warning signal_id (класс transient/durable + рекомендация UI). Тест-замок
  `test_status_protocol_registry.py` в обе стороны (неучтённый сигнал / призрак).
- **B** `stage_wait_heartbeat` (lifecycle/events.py): async-CM, эмитит info-WARNING
  {signal_id=stage_wait_heartbeat, stage, elapsed_ms} каждые
  `RunnerConfig.stage_heartbeat_seconds` (дефолт 10s, None=off). Врезки: llm_completion
  (весь _complete_request, включая лестницу ретраев) и tool_stage (весь тул-этап —
  прикрывает тул без TOOL_PROGRESS). 3 теста.
- **C** Политика ретрай-чаттера — § в status-protocol.md: движок не скрывает попытки,
  durable-сигналы показываются всегда (hermes-паттерн), буферизация — прерогатива хоста.
- **D** Спаны: `lifecycle_hook <name>` (kind CHAIN) вокруг каждого переопределённого
  finalize-хука в dispatch_finalize, таймаут = span ERROR; ретраи покрыты событиями →
  спанами хост-моста chat_v2.*.

## Фаза E — операторская приёмка 2026-07-23 (MeetScript dev, пин 1efdcbb7)

- Тяжёлый прогон (сводка по всем встречам Аргуса): heartbeat'ы llm_completion на
  10/20/30/40/50s; **максимальный немой интервал журнала 10.1s = интервал heartbeat**
  (критерий «нет немой стадии >N s» выполняется по построению).
- Phoenix: спан `lifecycle_hook rubric` (1190ms, kind CHAIN) на живом трейсе —
  грейдер цветной спан, дыры нет.
- Playwright (Принцип №2): в живом UI ярлык «Всё ещё жду ответ модели — Ns»
  появился на долгом ожидании и сменился следующим статусом при старте стриминга;
  «вечных» подписей нет. Хост-маппинг: ChatPage warning-ветка signal_id
  stage_wait_heartbeat → живой счётчик секунд.
