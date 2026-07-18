# Message-protocol hygiene plane: единый нормализатор истории

Дата создания: 2026-07-18. Статус: **done** (2026-07-19).

> Реализация: фаза A — `context_management/history_normalizer.py` (fold_tool_history /
> fold_tool_result_message переиспользуемы; провайдер-ретрай делегирует). Фаза B —
> `_preserve_orphan_tool_results` в триммере: обрезка внутри tool-пары больше не теряет
> улики — id-нёсущие сироты фолдятся в plain user-сообщения (id-less стабы легаси не
> трогаются). Фаза C — `preferred_history_view` (deepseek → folded): forced-final сразу
> использует folded view. **Ревизия 2026-07-19 по живому контрпримеру:** folded-FIRST
> регрессировал deepseek (canned-отказ на folded-виде в ~2/3 forced-final при
> работоспособном native) — порядок возвращён на native-first для всех профилей, а
> canned/wrong-language отказ (детектор эпика 015) теперь считается unusable-финалом
> на КАЖДОЙ ступени лестницы, так что fold всё равно срабатывает, когда native
> деградирует. Live N=3 после ревизии: decisions_log 3/3. Фаза D — closed_interrupted_tool_tail (незакрытый
> tool_calls-хвост → interrupted-стабы; отдельный close_tool_tail_before_user_injection
> для steering), repair битых JSON-аргументов, фолд внутрицикловых И внецикловых сирот
> в валидаторе (внецикловую дыру нашёл фаззер), детерминированный фаззинг 300 хвостов.
> Полная регрессия runtime/context/llm/contracts зелёная.

Источник: history-fold (86a4424) решил частный случай — deepseek пустеет на tool-протокольном
хвосте, свёртка в plain-сообщения спасает. Но фолдинг у нас живёт только как ПОСЛЕДНИЙ ретрай;
у референсов нормализация истории — штатная плоскость, применяемая к каждому исходящему виду.

## Reference-first

- **openclaude `src/utils/messages/`** (свежий рефакторинг, июль): `toolPairing.ts` —
  валидатор парности tool_use/tool_result (missing_tool_result, orphaned_tool_result,
  duplicate_*, server_tool_use_without_result) + `getToolPairSafeMessageRange` (обрезка окна
  ТОЛЬКО по безопасным границам пар); `apiTransform.ts` — подготовка trailing-истории
  (снятие snip-маркеров, плейсхолдеры вместо выброшенных блоков); синтетика через фабрику с
  провенансом (`SYNTHETIC_MODEL='<synthetic>'`).
- **hermes `agent/message_sanitization.py`** (477 строк): `close_interrupted_tool_sequence`
  (синтетический assistant, если хвост — сырой tool: `tool → user` ломает строгую альтернацию
  Gemini/Claude), image-stripping с сохранением tool_call_id-linkage, surrogate-скрубберы,
  `_repair_tool_call_arguments` для битого JSON локальных моделей.
- **hermes `agent/moa_loop.py:490-599`** — фолдинг как ШТАТНЫЙ view для строгих провайдеров:
  вся история → чистые user/assistant текстовые ходы (`[called tool: …]` / `[tool result: …]`,
  head+tail truncation), принудительный завершающий user-turn (trailing assistant = prefill
  для Opus). Ноль tool-role сообщений → строгие провайдеры не 400-ят.
- **hermes `agent/chat_completion_helpers.py:1376`** — плоский текстовый view ради
  prefix-cache hits.

## Чего не хватает agent-driver

`protocol_validate.py` покрывает часть (orphan drop, missing stubs), но:
1. Нет **safe-range trimming** — тримминг может резать посреди tool-пары (наш B-серии тримминг
   считает сообщения, не пары).
2. Нет **закрытия interrupted tool sequence** на живом хвосте (resume/steering-кейсы).
3. Фолдинг — не переиспользуемый view: нужен как **provider-profile capability**
   («strict-protocol провайдер → folded view всегда», а не только в 3-м ретрае).
4. Нет repair битых tool-аргументов (локальные модели) и image-stripping контракта.

## Фазы

A. Вынести history-fold из completion-ретрая в переиспользуемый normalizer-модуль; тесты
   переносятся.
B. Tool-pair validator + safe-range тримминг (интеграция с TrimmingSettings).
C. Provider-profile: `history_view: native|folded` в каталоге; deepseek-профиль → folded на
   force-final по умолчанию.
D. close_interrupted_tool_sequence + repair-паттерны hermes; фаззинг протокольных хвостов.
