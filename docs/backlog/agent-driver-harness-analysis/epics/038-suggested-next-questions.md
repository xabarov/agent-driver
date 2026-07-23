# Suggested next questions: подсказки следующего вопроса для ассистента встреч

Дата создания: 2026-07-23 (исследование референсов, раунд 2b). Статус: **DONE (A-D)** 2026-07-23.
Зависимость: эпик 034 (cache-safe форк-агент — исполнитель) или временно aux-seam эпика 032.

## Итог (2026-07-23)

- **A. Генерация.** `agent_driver/llm/suggestions.py`: `generate_suggestions(*, provider,
  question, answer, corpus_overview, model, max_suggestions, usage, ...) -> list[str]` +
  `filter_suggestion()` (порт openclaude `shouldFilterSuggestion`: assistant-voice/
  evaluative/meta/error-echo/prefixed-label/formatting/multi-sentence/длины, билингва
  RU/EN; вопросы РАЗРЕШены — в отличие от CLI-автокомплита) + кост-гейт
  `suppress_reason_for_usage()` (`MAX_PARENT_UNCACHED_TOKENS=10k`). Best-effort: `[]` на
  ошибке/пустом/all-filtered, НИКОГДА не бросает. `RuntimeEventType.SUGGESTED_QUESTIONS`.
  **Механизм генерации — плоское `provider.complete()` + построчный парсинг, НЕ
  форсированный tool-call:** сам референс генерит подсказку плоским текстом и фильтрует;
  live-замер подтвердил — `structured_completion` (эпик 036) не даёт tool-call через
  MeetScript privacy-провайдер + OpenRouter для gemini-flash-lite (`no emit_result tool
  call`) → генерация была бы всегда пустой. Урок вынесен в бэклог 036.
- **B. Кост-гейт + коалесинг.** Кост-гейт реализован (не генерировать при дорогом/
  некэшированном ходе). Коалесинг (superseded-abort из openclaude) структурно N/A для
  чипов-под-ответом: каждый ход генерит свои чипы синхронно в рамках хода, in-flight между
  ходами нет — новый ход просто вешает свежие чипы на свой ответ, чипы предыдущего остаются
  на предыдущем сообщении.
- **C. Хост (MeetScript).** `meet_script/chat_harness/suggested_questions.py` — тонкая
  обвязка: гейт `CHAT_V2_SUGGESTED_QUESTIONS` (dev on/prod off), вызов генератора после
  финализации ответа ТОЛЬКО на заземлённом ответе (не no_data/clarify/empty), bounded
  (`_TIMEOUT_MS`, fail-open), privacy-провайдер, aux-usage через прокси в общий
  `AuxUsageSink`. Результат в payload `run_completed` (SSE рвётся на `run_completed` →
  доставка только in-band). Фронт: `SuggestedQuestionsRow` под последним ответом ассистента,
  клик → `startChatTurn`.
- **D. Телеметрия + приёмка.** Raw-free эндпоинт `POST /chat_v2/{run_id}/suggestion_event`
  (`kind` shown/clicked, `count`) — зеркало feedback; фронт шлёт shown-on-render и
  clicked-on-pick. **Приёмка глазами оператора (Playwright):** на «Какие решения по покупке
  офиса?» под ответом отрендерились 3 чипа «Спросить дальше: Когда будет подписан договор о
  задатке? / Кто такой Максим? / Какова стоимость модернизации вентиляции?»; клик отправил
  новый ход, чипы исчезли с предыдущего (уже не последнего) ответа. Живьём aux учтён
  (`aux_calls`↑). Регрессий нет (свип 5/5 до фичи; фича аддитивна/fail-open).

Мотивация: витринная продуктовая фича для корпусного ассистента — после ответа предложить
1-3 кликабельных чипа следующего вопроса («Покажи action items этой встречи», «Кто владелец
решения по деплою?»). Снижает порог для нетехнического оператора (Принцип №2 MeetScript:
«вопрос оператора "как этим пользоваться?" = UX-баг» — чипы отвечают на него до того, как
он задан). openclaude везёт эту плоскость целиком, включая неочевидные грабли.

## Reference-first (openclaude `src/services/PromptSuggestion/`)

- **Философия генерации** (`SUGGESTION_PROMPT`): предсказывай, что ПОЛЬЗОВАТЕЛЬ набрал бы
  следующим («Would they think 'I was just about to type that'?»), а не что ему «стоит»
  спросить — иначе получаются менторские подсказки, которые никто не кликает.
- **Фильтр мусора** (`shouldFilterSuggestion`) — выстраданный reject-лист: оценочные
  («looks good»), голос ассистента («Let me…»), мета («nothing to suggest»),
  многопредложенческие, форматированные, слишком короткие/длинные; allowlist одиночных
  слов. Без фильтра фича генерирует стыд.
- **Кост-гейт** (`getParentCacheSuppressReason`, MAX_PARENT_UNCACHED_TOKENS=10k): НЕ
  генерировать после дорогого/некэшированного хода — подсказка не должна стоить как ответ.
- **Телеметрия принятия**: shownAt/acceptedAt/first-keystroke — единственный честный
  сигнал, тюнится ли промпт (доля кликов по чипам).
- Fire-and-forget из stop-хуков, `skipCacheWrite`, тот же префикс родителя (эпик 028/034).

## Эскиз фаз

A. Генерация: aux-вызов (дешёвая модель эпика 024-паттерна, gemini-2.5-flash-lite) после
   `run_completed`, вне критического пути (класс 2 терминального контракта); вход — вопрос
   + ответ + обзор корпуса; выход — 2-3 вопроса по-русски; порт reject-фильтра.
B. Кост-гейт + коалесинг (не генерировать при дорогом ходе; новый ход отменяет
   генерацию предыдущего — openclaude superseded-abort).
C. Хост: SSE-событие `suggested_questions` → чипы под ответом в ChatPage; клик = отправка.
D. Телеметрия показа/клика (raw-free: счётчики, не текст) + приёмка глазами оператора.

## Не в скоупе

Clarify-плоскость (интерактивные варианты ВНУТРИ хода) — отдельная отложенная тема
(аддендум 020 п.9); здесь только пост-ответные подсказки.
