# Tool-call wire integrity: never synthesize tool-call completeness

Дата создания: 2026-07-30 (horizon-scan 040, кандидат №3). Статус: **DONE (A-D)** 2026-07-30.

> Реализация: **A** `dedupe_tool_call_ids` в `extract_planned_tool_calls` (n-й дубль id →
> `<id>_d<n>`, детерминированно). **B** контроль пустого tool_calls в
> `_finalize_tool_stage_transition`: bounded re-prompt (≤3, сброс на успешном тул-раунде,
> сигнал `empty_tool_calls_contract_violation`); гейт — контент непригоден
> (`final_content_unusable`, не затирает содержательный ответ) И нет
> `suppressed_planned_tool_calls` (отличает пустой массив провайдера от рантайм-подавления при
> forced-final/budget). **C** гейт repair в `extract_planned_tool_calls`: при
> `finish_reason==UNKNOWN` вызовы с `text_form_args_repaired` отбрасываются (не исполнять
> половину команды при транспортном обрыве). **D** 12 тестов; полный свип **2810 passed**;
> signal_id и `empty_tool_calls_reprompt_count` зарегистрированы в status-protocol /
> runtime-metadata; CHANGELOG + ledger. Урок B: доменно-нейтральная граница — переспрашивать
> только непригодный контент; классификация «нарратив vs ответ» для непустого контента —
> забота консюмера (excel-ai/MeetScript).

Правило плоскости: **никогда не синтезировать завершённость tool-call'а**. Три
независимых wire-бага, каждый молча теряет/искажает вызов инструмента.

## Reference-first

- **(A / c) hermes `474c84ed8`**: провайдер переиспользует `tool_call_id` в одном батче →
  второй результат молча исчезал из реплея (tool-строки키ются по id, коллизия затирает).
  Фикс — детерминированный ресуффикс `<id>_d<n>` при инжесте (кэш-стабильный префикс).
- **(B / b) hermes `63954d508`**: `finish_reason="tool_calls"` с ПУСТЫМ массивом tool_calls
  (наблюдалось: opus-4.8 / sonnet-4.5 на GitHub Copilot) → петля берёт нарратив за
  финальный ответ, unattended-джоб «успешен» при `tool_turns=0` (scheduled PR-ревьюер
  каждый ран рапортует успех, ничего не сделав). Фикс — bounded re-prompt (3 подряд, сброс
  после успешного тул-раунда).
- **(C / a) openclaude `2fe1e1b`**: стрим оборвался без terminal reason при in-flight
  tool call → hard fail, а не «дочинить JSON»; repair усечённых аргументов только при
  провайдерском terminal reason.

## Текущее состояние (gaps подтверждены)

- **(c)** `tool_call_parser._to_tool_call` берёт `id` как есть; дедупа коллизий нет.
- **(b)** `_finalize_tool_stage_transition`: пустые envelopes → `continue_with_llm=False` →
  финализация. `finish_reason==TOOL_CALLS` с нулём planned-calls не отличается от честного
  финала.
- **(a)** `repair_tool_call_arguments_json` чинит усечённый JSON **безусловно**, без учёта
  finish_reason — усечённый транспортом tool call дочиняется и исполняется.

## Фазы

A. **(c) Дедуп `tool_call_id`.** Хелпер `dedupe_tool_call_ids` в единой точке
   материализации `extract_planned_tool_calls`: n-й дубль id → `<id>_d<n>`
   детерминированно (порядок вызовов). Executor и протокол видят один и тот же
   уникальный id → второй результат не теряется. Кэш-стабильно (детерминизм).
B. **(b) Пустой tool_calls контракт.** В `_finalize_tool_stage_transition`: если
   `finish_reason==TOOL_CALLS`, нет envelopes И нет planned-calls И нет text-form вызовов в
   content → контрактное нарушение: bounded re-prompt (`empty_tool_calls_reprompt_count`,
   ≤3 подряд, сброс после успешного тул-раунда), сигнал `empty_tool_calls_contract_violation`.
   Иначе — вечно молчаливый unattended-провал.
C. **(a) Не исполнять достройку при не-терминальном обрыве.** В `extract_planned_tool_calls`:
   при `finish_reason==UNKNOWN` (нет terminal reason — транспортный обрыв) отбросить вызовы с
   маркером `text_form_args_repaired` (синтезированы из усечённого JSON) — не исполнять
   половину команды; вернуть как parse-error, петля переспросит.
D. **Приёмка.** Юниты на каждое правило + полный свип, CHANGELOG, статус, ledger.

## Не в скоупе

- Полный streaming-ассемблер нативных tool-calls с terminal-reason-гейтом на уровне
  провайдера (openclaude делает это в shim) — у нас усечение приходит текст-формой/метадатой;
  правило (a) закрывает исполняемый риск на инжесте. Провайдер-специфичный shim — при росте
  числа провайдеров.
