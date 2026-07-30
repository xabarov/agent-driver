# Transcript-poisoning hygiene: CoT никогда не персистится + один владелец empty-repair

Дата создания: 2026-07-29 (horizon-scan 040, кандидат №1). Статус: **DONE (A-E)** 2026-07-30.

> Реализация: **A** `llm/reasoning_hygiene.strip_leading_think_block` — inline `<think>`
> вычищается на двух входах в реплеиваемую историю (assistant-чекпоинт протокола в
> `tool_stage`, терминальный ответ в `finalization/output`); streamed reasoning-канал и
> redirect-чекпоинт уже были CoT-free (аудит). **B** `llm/message_hygiene.repair_empty_non_final_messages`
> — единый pre-send владелец (верх retry-цикла `complete_request` + aux-путь `llm.aux`),
> copy-on-write, идемпотентно, designed-empty (tool_calls/echo/tool-rows) не трогает; в llm-слое
> во избежание инверсии, ре-экспорт из `provider_requests`. **C** `contracts/scaffolding` —
> один тег (persistence/compaction/display); синтетические USER-ходы помечены, настоящий
> redirect-correction — нет; partial-компакция переклеивает роль в `runtime`, llm-full выкидывает
> из эксцерпта. **D** `message_hygiene.quarantine_inline_reasoning` — bounded quarantine-ступень
> в ладдере 016 перед честным сигналом (`poisoned_prefix_suspect`, ретрай ×1). **E** 28 тестов;
> полный свип **2788 passed** (3 пред-существующих падения: `test_chat` budget-loop + 2
> `phase6_metadata`, воспроизводятся на чистом baseline). CHANGELOG обновлён; signal_id и
> metadata-ключи зарегистрированы в status-protocol / runtime-metadata.

Мотивация: у hermes это инцидент-класс, который **окирпичивает сессию навсегда** —
и наш steering v2 (030) + empty-recovery ladder (016) создают ровно ту же поверхность.
Три правила гигиены транскрипта, каждое дешёвое, вместе закрывают класс.

## Reference-first

- **hermes `cf0c42fa0` (самый ценный коммит дельты 07-23..07-29)**: `/steer`-редирект во
  время thinking-фазы сериализовал streamed reasoning в персистентный чекпоинт ассистента
  («Reasoning shown before the interruption: …»). Ассистентский ход, показывающий
  собственный CoT, читается провайдерским классификатором как reasoning-injection/prefill
  → **каждый последующий вызов сессии возвращает empty, перманентно**; чекпоинт
  персистентный, поэтому ни retry, ни nudge, ни empty-ladder не спасают. 4 сессии
  окирпичены за неделю, 42+ заблокированных вызовов. Класс-фикс: streamed reasoning —
  display-only состояние; аккумулятор удалён.
- **hermes `309f06b04`/`725c7ba53`**: у них было ЧЕТЫРЕ форкнутые реализации «не отправлять
  пустой non-final ход» (DB-write pad, loop send pad, stream-stub подстановка, pre-send
  санитайзер). Оставлен один владелец — `repair_empty_non_final_messages` внутри
  `sanitize_api_messages`: безусловный pre-send chokepoint, общий для main-петли И
  aux/summary-пути; только non-final; copy-on-write; payload-предикат расширен, чтобы
  designed-empty ходы (item-carriers) не переписывались. Негативный сигнал: ранняя
  стаб-подстановка `[response interrupted]` откачена ими самими — ломала empty-stub guard
  петли и утекала в сшитый финальный ответ. Портировать финальную форму, не первую.
- **hermes `923704c7c`**: синтетическая nudge-пара, добавленная без scaffolding-тега,
  после resume реплеилась как user-authored контекст, а компрессор читал её как
  человеческое намерение. Фикс — тег в ДВУХ реестрах (`_EPHEMERAL_SCAFFOLDING_FLAGS`
  для persistence, `_SYNTHETIC_USER_FLAGS` для компрессора). Правило: **каждому
  синтетическому сообщению — тег, который чтят persistence, compaction и display
  projection одновременно.**
- **hermes `c883367bd`**: interrupt-checkpoint скаффолдинг — server-only `api_content`
  сайдкар + `display_kind=hidden` (реплеится модели, скрыт от всех транскриптных
  поверхностей), а не запись в видимый контент.
- **hermes `214ae7b77`** (граница правила): при ПОЛНОСТЬЮ исчерпанном empty-ladder, если
  есть structured reasoning без видимого текста, — доставить помеченный экскерпт.
  Безопасно только потому, что это delivery-only: в транскрипт не пишется, в ладдере
  раньше не промоутится.

## Наша поверхность (аудит-точки)

- `runtime/single_agent/streaming.py` / `llm_step/completion.py`: `_append_reasoning_details`,
  `emit_reasoning_delta_events`, `strip_reasoning_echo` (encrypted-echo retry уже есть).
  Разграничить: (а) plaintext CoT → только stream-события/display, никогда в `content`
  персистентного assistant-хода; (б) encrypted reasoning echo — провайдерский контракт,
  отдельный канал, уже со strip-ретраем.
- Steering 030: INTERRUPT/REDIRECT во время thinking — чекпоинт не должен захватывать
  reasoning-текст (наш аналог `cf0c42fa0`).
- `llm_step/provider_requests.py`: `empty_forced_final_no_tools`, `empty_forced_final_history_fold`
  — империческая обработка пустоты разбросана; кандидаты на единый pre-send владелец.
- `tool_stage/__init__.py`: `_append_denial_recovery_message`, `_append_unknown_tool_recovery_message`
  + `runtime_retry`-метаданные — инвентаризация синтетических сообщений под единый тег.

## Эскиз фаз

A. **CoT-инвариант.** Аудит всех путей, где reasoning-текст может попасть в персистентную
   историю (stream-аккумуляторы, steering-чекпоинты, finalize/answer_recovery, журнал).
   Инвариант-тест: ни при каком прерывании (steer во время thinking, отмена, таймаут)
   plaintext reasoning не оказывается в `content` реплеиваемого сообщения. Encrypted
   echo — вне инварианта (провайдерский канал).
B. **Единый pre-send владелец.** `sanitize_api_messages`-аналог в одном chokepoint'e
   (main-петля + aux/компакция/грейдеры): repair пустых non-final user/assistant ходов,
   copy-on-write, payload-предикат с учётом designed-empty (tool-carriers). Убрать/свести
   разрозненные empty-подпорки к вызову владельца.
C. **Scaffolding-тег контракт.** Реестр синтетических сообщений (denial recovery, unknown
   tool recovery, nudges, forced-final) + единый тег в metadata; тесты: persistence не
   отдаёт их как user-authored после resume, компакция не читает их как намерение
   пользователя, display projection скрывает. (Смежно 035 re-inject — сверить теги.)
D. **Poisoned-prefix детекция в ладдере 016.** Если ladder исчерпан И серия empty началась
   сразу после конкретного assistant-хода — диагностический сигнал `poisoned_prefix_suspect`
   (кандидат-ход в payload) вместо бесконечного FAILED-цикла; опционально quarantine-ветка:
   ретрай с вырезанным подозрительным ходом (bounded, 1 раз), по образцу нашего же
   `strip_reasoning_echo`-ретрая. Delivery-only экскерпт reasoning (`214ae7b77`) — как
   последняя ступень ладдера, без записи в транскрипт.
E. **Приёмка.** Юнит на каждый инвариант; интеграционный: steer-во-время-thinking →
   сессия продолжает отвечать (репро-сценарий hermes); полный свип зелёный; CHANGELOG.

## Не в скоупе

- Wire-integrity оборванных tool-call'ов — кандидат 042 (пересекается в стриме, но
  другое правило: «не синтезировать завершённость»).
- Liveness/heartbeat — кандидат 041.
- Domain-фильтры контента ответов — хосты (excel-ai/MeetScript).
