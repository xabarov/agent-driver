# Многослойная компакция: tiered-сжатие tool-истории, time-based клиринг, span-collapse

Дата создания: 2026-07-23. Статус: **DONE (A-E)** 2026-07-24.
Зависимость: эпик 034 (форк-агент — исполнитель span-collapse).

## Итог (2026-07-24)

Инвентаризация (урок 033) показала ~90% заскаффолжено: 5 слоёв (E5 tool-arg-trunc,
microcompaction, session-memory, llm_full, partial) + оркестратор с брейкером +
9-секционный summary-шаблон + RU-язык + protect_recent_turns + aux/fork доступны.
Закрыты реальные gap'ы:

- **A. Tiered tool-history компрессия** (`context/compaction/tool_history.py`, openclaude
  `compressToolHistory`): старый tool_result-bulk сжимается по тирам (recent full →
  mid truncate с length-маркером → old stub), идемпотентно, structure-preserving, для
  stateless/no-cache провайдеров. Подключено LLM-free pre-pass'ом за
  `CompactionSettings.enable_tool_history_compression` (default off); аудит
  `tool_history_compression` в metadata+инвентаре.
- **B. Idle clear-keep** (`tool_clear.py`, openclaude `microCompact`): binary-clear
  старого tool-контента по `keep_recent` с маркером `[Old tool result content cleared]`
  + idle-gap триггер-хелпер. Чистые функции, идемпотентно.
- **C. Span-collapse ПРИМИТИВЫ** (`span_collapse.py`, openclaude `contextCollapse`):
  protect first-turn framing + `PROTECTED_TAIL_RATIO` working-set, turn-start границы
  (пары не рвутся), `COLLAPSE_TARGET_RATIO` sizing, risk=0.5·age+0.5·size, placeholder
  `<collapsed id>`. Чистые/тестируемые; **live форк-исполнение отложено с причиной**
  (самый дорогой слой + инертен для хоста + разделяет empty-message-set риск-семейство,
  из-за которого chat_v2 отключил компакцию).
- **D. Re-inject инвариант**: `apply_post_compact_cleanup` — ЕДИНАЯ точка, держащая ВСЁ
  steering-состояние живым после компакции; добавлены goal-gate rubric-снапшот +
  bounded recalled-memory блок (переживали лишь ИНЦИДЕНТНО) к существующим planning-
  state + artifact-refs. В инвентаре.

aux-wiring для llm_full НАМЕРЕННО не сделан: компакция шлёт self-contained промпт (нет
parent-префикса → `AuxCachePrefix` неприменим), а стадия уже учитывает cost.

**Хост D/E**: chat_v2 держит компакцию OFF (load-bearing — баг пустого message-set на
корпус-контексте). Реальный host-win — **history-компакция** (гейт
`CHAT_V2_HISTORY_COMPACTION`, dev on/prod off): дропаемые старые ходы длинной сессии
сворачиваются в компактный ведущий маркер (свёрнутые user-вопросы), чтобы антецеденты
анафоры («а почему от второго отказались?») переживали границу окна. Детерминированно,
без LLM → «Input required»-класс невозможен (текст диалога, не корпус-контекст).
Приёмка: 22 движковых теста + 23 host-теста (fold-маркер on/off); модули live в образе;
**живой чат-ход штатный (1203 симв)** — регресса от гейта нет.

## Урок

Как и 033 — инвентаризовать движок ПЕРЕД реализацией. Для MeetScript слои A/B/C инертны
(chat_v2 компакция OFF; короткая история single-turn). Реальный host-рычаг — не движковые
слои, а host-side свёртка дропаемых ходов диалога. Span-collapse live-форк отложен как
самый дорогой + рискованный слой (029/032-прецедент частичной сдачи с причинами).

Мотивация: наш context-план (017) — детерминированный trimming с protect_recent_turns.
Референсы показывают более зрелую архитектуру: НЕСКОЛЬКО взаимодополняющих слоёв компакции,
каждый со своим триггером и стоимостью, вместо одного бинарного «резать/не резать». Для
MeetScript-чата (длинные сессии с крупными retrieval-вставками) это прямой рычаг качества
многоходовых диалогов.

## Reference-first (openclaude, все слои сосуществуют)

- **`src/services/api/compressToolHistory.ts`** — size-based tiered сжатие СТАРЫХ tool_result
  на уровне провайдер-шима для stateless (не-Claude, без prompt-cache) провайдеров:
  mid-tier truncation → stub с сохранением исходной длины в маркере, выброс inline-картинок,
  структура пар tool_use/tool_result неприкосновенна. Идемпотентно; тиры масштабируются от
  эффективного окна. Прямо про наш provider-fallback: уход на дешёвый бэкенд не взрывает историю.
- **`src/services/compact/microCompact.ts`** — time/idle-based clear/keep старых tool-результатов
  по белому списку `COMPACTABLE_TOOLS`, маркер `[Old tool result content cleared]`. Дёшево,
  без LLM.
- **`src/services/contextCollapse/`** — risk-scored свёртка ОДНОГО старейшего диапазона целых
  ходов через форк-агента: защита первого хода (framing) и хвоста (PROTECTED_TAIL_RATIO=0.3),
  границы по началам ходов (пары tool_use/result не рвутся), состояния staged→committed,
  health-метрики. Самый дорогой слой, включается последним.
- **`src/tools/SnipTool/`** — агентная саморегуляция: модель сама помечает отработавшие куски
  (тупиковые поиски, решённые ошибки) на удаление по snip_id. Кандидат-направление, не ядро.
- **hermes `tools/todo_tool.py`** — инвариант выживания планов: рабочий список ре-инжектится
  после КАЖДОГО события компакции (с жёсткими бортами на размер) — образец для любого
  нашего steering-состояния, которое обязано переживать компакцию.
- **openclaude `src/utils/doomLoop.ts`** — смежная дельта к 019: сигнатура
  `toolName::sha256(input)` с ключом ПО АГЕНТУ (main и каждый субагент отдельно) против
  ложных срабатываний при параллельных субагентах — сверить наш loop-guard.

## Эскиз фаз

A. **Слой шима**: tiered-сжатие старой tool-истории для провайдеров без кэша (идемпотентное,
   структуро-сохраняющее) — включается при fallback/routing на stateless-бэкенд.
B. **Слой clear/keep**: time/idle-based клиринг старых tool-результатов по белому списку,
   с маркером; конфиг порогов per-host.
C. **Слой span-collapse** (на 029): выбор и свёртка старейшего спана форк-агентом,
   staged→committed, protect-первый-ход + protect-хвост (совместить с protect_recent_turns
   из 017), health-метрики в runtime metadata.
D. **Инвариант ре-инжеста**: контракт «steering-состояние переживает компакцию» (планы,
   goal-гейт, memory-снапшот) — единая точка, а не per-фича костыли.
E. Приёмка: длинные многоходовые сессии MeetScript (диалоговый сабсет бенча + Аргус) без
   регрессий; замер: доля окна, возвращённая каждым слоем; B17-класс (антецеденты
   ассистента) не ломается свёрткой.

## Не в скоупе

Deferred-каталог и spill tool-выводов (028), базовый trimming (017 — остаётся последним
рубежом), выбор модели для форка (012/029).

## Дополнение 2026-07-23 (раунд 2b) — качество саммари из обоих референсов

- **hermes шаблон** (`context_compressor.py:2496-2684`): «## Active Task» — главная
  секция, ловится ДОСЛОВНО («the exact words they used»); reverse-signal (стоп/отмена
  задачи фиксируется дословно, отменённое НЕ переносится); `HISTORICAL_*`-заголовки
  вместо «Next Steps», чтобы слабая модель не читала старую работу как свежие
  инструкции; **временно́е заякоривание** (сегодняшняя дата + завершённые действия в
  датированный past-tense); итеративное обновление при повторных компакциях;
  focus-topic 60-70% бюджета; **«пиши саммари на языке пользователя»** (RU!);
  секрет-гигиена [REDACTED] прямо в промпте.
- **hermes pre-prune**: `_summarize_tool_result` — информативные 1-строчники вместо
  нулевых плейсхолдеров («[retrieval] запрос 'X' → 6 встреч, топ: <названия>»);
  `_strip_historical_media`; `_truncate_tool_call_args_json` сохраняет валидный JSON.
- **hermes retry-too-large дисциплина**: порог меряется по «пересёк ли ЗАПРОС окно», а
  не «сжался ли список» — system+схемы фиксированы, и если они сами не влезают,
  компакция бессильна и честно говорит об этом; abort-and-preserve при сбое aux-модели.
- **openclaude схема**: 9 фиксированных секций, «все user-сообщения ДОСЛОВНО» +
  цитируемый next-step; `<analysis>`-черновик, вырезаемый перед входом в контекст;
  указатель на полный транскрипт на диске; продолжение «Resume directly — не
  подтверждай саммари»; **партиал двух направлений** from/up_to — сжать хвост, сохранив
  кэшируемую голову (наш RAG-кейс: стабильная retrieval-голова + черновой Q&A-хвост).
