# Cost-плоскость: роллапы, aux-task учёт, видимость пользователю

Дата создания: 2026-07-23 (исследование референсов, раунд 2). Статус: **proposed**.

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
