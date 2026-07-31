# Context-engine seam: per-category breakdown + fail-open select-context

Дата создания: 2026-07-30 (horizon-scan 040, кандидат №4). Статус: **DONE (A-D)** 2026-07-30.

> Реализация: **A** `context/breakdown.py::estimate_context_breakdown` — char/4 по
> категориям system_prompt/tool_definitions/tool_results/scaffolding/conversation; итог
> `total_chars//4` == триггер компакции; принимает ChatMessage и dict'ы. **B** fail-open
> `dispatch_before_llm`: `_is_degenerate_request` отбрасывает replacement без не-system
> сообщений (ловушка `all([]) is True`) → падаем на предыдущий запрос. **C**
> `context_breakdown` в терминальной метадате (`_safe_context_breakdown`, из
> `protocol_messages`, fail-open). **D** 11 тестов; полный свип **2821 passed**; CHANGELOG +
> ledger. Механика `select_context`/`on_turn_complete` уже была через
> `before_llm_request`/`on_run_completed` — 044 добавил только safety (B) и наблюдаемость (A/C).
> Продуктовая примерка retrieval-плагина — на хосте.

Мотивация (hermes `agent/context_engine.py` + `context_breakdown.py`): дать хостам
штатный шов «подмени состав ЭТОГО промпта, не мутируя историю» + честную оценку состава
следующего запроса ПО КАТЕГОРИЯМ той же эвристикой, что и порог компакции (UI-число ==
триггер). Для RAG-хостов (MeetScript, excel-ai) — вместо злоупотребления `should_compress()`.

## Что уже есть (не переделываем)

- **`select_context`-механизм** — `RunLifecycleHook.before_llm_request(context, request)`
  возвращает replacement-`LlmRequest`: трансформирует ЗАПРОС (retrieval/routing/ветки), не
  мутируя `run_input.messages` → история персистентна. `dispatch_before_llm` fail-open на
  исключениях (упавший хук пропускается).
- **`on_turn_complete`-механизм** — `after_llm_response` (пост-ответ) + `on_run_completed`
  (пост-ран индексация). Пост-ходовая индексация для следующего select покрыта.

## Незакрытые gaps (этот эпик)

- **Оценка состава по категориям** — `estimate_token_pressure` даёт агрегат
  (`(prompt_chars)//4`), но НЕ разбивку system/tools/tool_results/scaffolding/conversation.
  Хост не видит, ЧТО занимает окно (нельзя показать `/context`, нельзя целить retrieval).
- **Fail-open дегенеративного replacement** — `dispatch_before_llm` fail-open на raise, но
  НЕ на дегенеративный результат: select_context, отфильтровавший всё (ловушка hermes
  `all([]) is True`), возвращает запрос с пустыми messages → модель получает пустой промпт.

## Фазы

A. **`context/breakdown.py`** — `estimate_context_breakdown(messages, tools=None)`:
   разбивка char/4 по категориям `system_prompt / tool_definitions / tool_results /
   scaffolding / conversation`. Итог `total_tokens = total_chars // 4` совпадает с
   триггером компакции (авторитетное число); пер-категория — для отображения. Юниты.
B. **Fail-open hardening** `dispatch_before_llm`: replacement, у которого нет ни одного
   не-system сообщения (или не `LlmRequest`), логируется и ОТБРАСЫВАЕТСЯ — падаем открыто
   на предыдущий запрос. Ловушка `all([]) is True` закрыта. Юниты.
C. **Экспозиция** `context_breakdown` в терминальную метадату рана (эквивалент `/context`):
   хост читает те же категории/число, что видит триггер. Юнит.
D. **Приёмка** — полный свип, CHANGELOG, статус, ledger.

## Не в скоупе

- Полноценный `ContextEngine` ABC как отдельный тип (hermes) — у нас шов уже выражен через
  `RunLifecycleHook`; вводить второй параллельный протокол = дублирование. Продуктовая
  примерка (retrieval-плагин на MeetScript/excel-ai) — на стороне хоста.
- `select_context()` как отдельный метод протокола — `before_llm_request` покрывает
  механику; 044 добавляет к нему только safety (B) и наблюдаемость (A/C).
