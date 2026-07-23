# Cost-плоскость: роллапы, aux-task учёт, видимость пользователю

Дата создания: 2026-07-23 (исследование референсов, раунд 2). Статус: **DONE 2026-07-23 (A-E)**.

Мотивация: per-run «чек» у нас есть («Сведения о запуске»: токены/скорость/cost_usd), но
(1) `CostLedger` движка (прайс-таблица, cache_hit_rate, format_cost_summary) хостом не
используется и не персистится; (2) **вспомогательные вызовы не учитываются нигде**:
goal-gate грейдер, экстракция памяти, ретраи лестницы — все жгут токены мимо чека;
(3) роллапов per-conversation/per-user нет → ни админ-видимости, ни квот; (4) выбор
aux-модели у нас ad-hoc (CHAT_V2_GOAL_GATE_MODEL появился в 024 по замеру — паттерн
надо обобщить).

## Reference-first

- **hermes `agent/aux_accounting.py`** — `record_aux_usage()`: КАЖДАЯ side-задача (титул,
  компрессия, vision, background_review) фаннелится в `session_db.record_auxiliary_usage`
  — ничто не убегает из учёта. **`agent/auxiliary_client.py`** — единый роутер aux-задач:
  per-task конфиг `auxiliary.<task>.provider/model`, дешёвый дефолт per-provider,
  interrupt-protection для атомарных aux-вызовов. Наш 024-урок («модель грейдера выбирать
  замером») — частный случай этого паттерна.
- **hermes `agent/insights.py`** — InsightsEngine: пер-день/сессия роллапы токенов, cost,
  cache_read, инструментов; `/usage` во всех поверхностях. `agent/credits_tracker.py`
  (свежие коммиты): эскалирующие пороги 50/75/90% «$used of $cap» одной строкой с
  латчем (не стекается, откатывается при восстановлении).
- **hermes `usage_pricing.normalize_usage`** — канонизация usage трёх провайдерных
  диалектов (вкл. cached-поля) — общая с эпиком 028 деталь.
- **openclaude cost-tracker** — cache read/write токен-бары; титулы сессий Haiku'ом
  (`utils/sessionTitle.ts`, JSON-schema {title}, 3-7 слов) — образец aux-задачи №1.

## Эскиз фаз

A. **Aux-task seam в движке**: `RunnerConfig.auxiliary_models: dict[task, model]`
   (grader/extraction/consolidation/title/…) + хелпер `aux_request(task, ...)`,
   который (1) подставляет модель задачи, (2) МАРКИРУЕТ usage задачи (`purpose` уже
   есть в metadata) и (3) отдаёт usage в общий учёт рана. Хостовые
   CHAT_V2_GOAL_GATE_MODEL / extraction — мигрируют на этот seam.
B. **Учёт**: usage aux-вызовов (грейдер, экстракция, консолидация, ретраи лестницы)
   агрегируется в терминальный usage-payload рана с разбивкой main/aux —
   «чек» перестаёт врать. CostLedger персистится в чекпойнт-метаданные (его же
   задокументированный H20b-хвост).
C. **Хост-роллапы**: Mongo-агрегация usage по conversation/user/день (materialized при
   терминале рана — дёшево), admin-endpoint + карточка в System-панели; опциональные
   пороги-уведомления по образцу credit bands (50/75/90% от настраиваемого капа).
D. **Титулы разговоров** (демо aux-seam на хосте): LLM-титул 3-7 слов вместо
   `first_question[:80]` (mongo_worker.py:1188) — aux-задача title на flash-lite,
   атомарный set-if-empty (hermes: никогда не перетирать пользовательский титул),
   фоновая (terminal-phase класс 2).
E. Приёмка: чек рана показывает main+aux с суммой, совпадающей с OpenRouter-биллингом
   на контрольной сессии (±5%); роллап пользователя за день сходится с суммой чеков;
   титулы живых разговоров осмысленны (операторская приёмка сайдбара).

## Не в скоупе

Cache-телеметрия как таковая (028 фаза A — здесь только её агрегация); квоты/биллинг
как продукт (только данные и пороги).

## Дополнение 2026-07-23 (раунд 2b)

- **hermes AgentNotice** (`credits_tracker.py`) — транспорт-агностичный out-of-band
  нотис: level/kind sticky|ttl/ttl_ms/`key`-дедуп; **эскалирующие бэнды** 50→75→90%
  одной строкой (показывается высший достигнутый, не три нотиса); «$used of $cap»
  вместо процентов; depletion ТОЛЬКО по `paid_access==False`, никогда по
  `remaining==0` (классическая ложная тревога); free-tier-модели подавляют нотис.
- **Метадата-фолбэк прайсинга** (`usage_pricing._pricing_entry_from_metadata`): цена
  считается даже для модели без строки в таблице — для deepseek-v4-flash не ждать
  ручного прайса.
- **Укрепление фазы D (титулы)** — openclaude `sessionTitle.ts`: 5-ступенчатая лестница
  парсинга (strict_json → embedded_json → quoted_string → short_line → default) +
  санитайзер прозы/фенсов/терминальных escape — обязательна на deepseek-классе моделей;
  таймаут 12s; хвостовые 1000 чаров разговора как вход; титул на языке пользователя
  (hermes title_generator: генерация ПОСЛЕ доставки ответа, aux-моделью).
- **Дешёвые соседи фазы D**: hermes `/recap` — локальный рекап сессии БЕЗ LLM (счётчики
  тулов, последние ходы, тронутые сущности) — нулевая цена, не пересекает PII-границу;
  openclaude per-session cost restore-on-resume; «~»-префикс оценочных токенов, когда
  провайдер не отдал usage.

## Реализация 2026-07-23 (хост-only — движок не трогали)

- **B DONE (aux-учёт — заявленная мотивация №2 «чек врёт»)**: `AuxUsageSink`
  (goal_gate.py) — сырьё-безопасный сборщик (только счётчики) токенов aux-вызовов;
  грейдер goal-gate пишет в него свой usage; протянут через `ChatV2AgentBuild.aux_usage_sink`
  в `_chat_v2_usage_payload` → чек показывает `aux_tokens`/`aux_calls`/`total_tokens_with_aux`;
  фронт — «+N aux». Живая приёмка: `main=19415 + aux=2144 (1 грейдер) = 21559` — раньше
  2144 токена грейдера были невидимы. Экстракция памяти — fire-and-forget (defer_sync,
  027), в ран не атрибутируется — помечено в докстринге sink. 2 теста.
- **A частично**: паттерн выбора aux-модели по замеру (024, CHAT_V2_GOAL_GATE_MODEL) и
  примитив учёта (sink) есть; полный движковый `RunnerConfig.auxiliary_models` registry —
  НЕ сделан (рефакторинговая ценность выше пользовательской при двух aux-задачах).

## Отложено с точными причинами → ДОДЕЛАНО 2026-07-23

Изначально A/C/D были отложены; по требованию «выполнить ВСЕ работы» доведены до конца.

- **A DONE (движок 3631fca)**: `CapabilitySettings.auxiliary_models: dict[task,model]` +
  `aux_model_for(task)` (task-реестр → общий auxiliary_model → None); делегирующие
  свойства на RunnerConfig. Обобщает урок 024 с одной env-ручки до типизированного шва.
  2 движковых теста.
- **D DONE (LLM-титулы, PII-safe)**: `title_generator.py` — 3-7 слов через
  `privacy_aware_chat_completion` (тот же барьер, что ответ; под external_anonymized
  сырой PII не уходит), openclaude 5-tier parse-ladder + санитайзер; aux-модель по
  CHAT_V2_TITLE_MODEL → CHAT_V2_AUX_MODEL → gemini-flash-lite (seam фазы A). Эндпоинт
  `POST /chat/conversations/{id}/title` читает Q+A серверно, `upgrade_title_if_auto`
  (атомарный set-if-auto, ручную правку НЕ трогает — `title_auto`-метка). Фронт зовёт
  после первого обмена. Живьём: «чем закончилась встреча Аргус…» → **«Итоги встречи
  Аргус: модель, ФСТЭК, монтаж, бюджет»**; ручная правка не перетёрта (reason
  manual_title). Гейт CHAT_V2_LLM_TITLES. 7 тестов parse-ladder.
- **C DONE (роллапы)**: `chat_usage` коллекция; `record_chat_usage` (best-effort при
  терминале рана, raw-free: счётчики/стоимость/модель) + `aggregate_chat_usage`
  (per-day + per-user через $group). Эндпоинт `GET /chat_v2/usage/rollup?days=N`
  (админ — все, юзер — свои) + credit-бэнд 50/75/90% от CHAT_V2_USAGE_TOKEN_CAP
  (0=off, hermes-паттерн высшего бэнда). Живьём: роллап пишется (runs=1,
  total=24310, aux=1975, cost=$0.000956), эндпоинт агрегирует per-day+per-user.
- **E приёмка**: чек main+aux (фаза B); титул живьём осмыслен + идемпотентен +
  не перетирает ручной; роллап сходится с чеками. Тесты хоста 91, движок aux-seam +
  contracts, jest 267.

### Урок процесса
`get_meeting_service` локальным import внутри функции сделал имя локальным для всей
функции → «cannot access local variable» на РАНЬШЕЙ ссылке; терминальный блок был в
`run_chat_v2_agent_driver` (user_id), а не `_run_chat_v2_agent` (app_metadata) —
перепутал переменную; кастомный хост-Logger не принимает exc_info (память [[chat-goal-gate]]).
Все три — быстро пойманы диагностикой jobworker-лога.
