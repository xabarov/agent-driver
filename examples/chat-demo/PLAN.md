# Chat Demo — план работ

Демонстрационное и тестовое приложение для `agent-driver`: FastAPI-бекенд
поверх `agent_driver.sdk` + современный React-чат, визуально и по UX близкий к
OpenRouter / Anthropic Claude UI / ChatGPT.

Назначение:

- быстрый smoke / acceptance прогон фич библиотеки (стриминг, tools, HITL,
  resume, replay, sessions, providers) без CLI;
- эталонный пример встраивания `agent-driver` для внешних пользователей;
- площадка для ручной QA новых фич runtime и tools пакетов.

---

## 1. Цели и не-цели

### 1.1. Цели

- покрыть **руками и глазами** ключевые сценарии `agent-driver`:
  - streaming ответа модели (`TOKEN_DELTA`);
  - произвольные tool calls c карточками `tool_call_started` /
    `tool_call_completed`;
  - HITL: `interrupt_requested` → approve / reject / edit / cancel / clarify;
  - durable resume по `run_id` после рестарта бекенда (sqlite store);
  - переключение провайдеров (`fake` / `openrouter` / `vllm` / `ollama`);
  - выбор tool surface (ToolSet, packs, max risk);
  - replay прошлого `run_id` (просмотр событий из durable event log);
  - сессии: список / возобновление / экспорт `.md`.
- сделать UI, не стыдный для демо клиенту: тёмная / светлая тема, читабельные
  бабблы, корректный markdown + подсветка кода, плавный токен-стрим, mobile-
  friendly.

### 1.2. Не-цели (первая итерация)

- не реализуем мультиюзер auth / SSO / RBAC — single-user локально;
- не пишем production-grade хранилище — sqlite-стор `agent-driver`'а
  достаточно;
- не интегрируем платёжки, биллинг, телеметрию третьих сторон;
- не делаем mobile native apps;
- не реализуем встраиваемые редакторы кода / canvas / артефакт-просмотрщики
  типа Claude artifacts (вынесено в "Out of scope" ниже).

---

## 2. Архитектура

```
+----------------------------+        SSE (text/event-stream)
|                            | <-------------------------------+
|  React + Vite + TS         |        REST (JSON)               |
|  (frontend/)               | <-------------------------------+ +
|                            |                                 | |
+-------------+--------------+                                 | |
              |                                                v v
              |     +---------------------------------------------------+
              +---> |  FastAPI (backend/)                               |
                    |                                                   |
                    |   /api/chat/runs            POST  (start run)     |
                    |   /api/chat/runs/{id}/stream  GET  (SSE)          |
                    |   /api/chat/runs/{id}/resume  POST (HITL)         |
                    |   /api/sessions             GET / POST            |
                    |   /api/sessions/{id}        GET                   |
                    |   /api/sessions/{id}/replay GET                   |
                    |   /api/tools                GET                   |
                    |   /api/providers            GET                   |
                    |   /api/health               GET                   |
                    |                                                   |
                    |   uses:                                           |
                    |     agent_driver.sdk.create_agent(...)            |
                    |     agent_driver.adapters.sse_event_stream(...)   |
                    |     agent_driver.runtime.*                        |
                    +-----------------+---------------------------------+
                                      |
                                      v
                          sqlite runtime store (.agent-driver/chat-demo.db)
                          + JSONL session store (.agent-driver/sessions/)
```

Ключевая идея: бекенд **тонкий**, он не дублирует логику CLI или SDK, а
переиспользует:

- `agent_driver.sdk.create_agent` для сборки рантайма;
- `agent_driver.runtime.create_runtime_store_bundle` с
  `AGENT_DRIVER_RUNTIME_STORE_KIND=sqlite` для durable события и checkpoints;
- `agent_driver.adapters.sse_event_stream` для нормализованных SSE-фреймов
  (event/id/data/retry, поддержка `Last-Event-ID` reconnect);
- `agent_driver.adapters.cli_replay_lines` для текстового replay (HTML-вариант
  делает фронтенд по сырым `RunStreamEvent`).

---

## 3. Структура директорий

```
examples/chat-demo/
├── PLAN.md
├── README.md
├── .env.example
├── .gitignore
├── docker-compose.yml
├── Makefile                    # удобные команды dev/test/lint/build
├── backend/
│   ├── pyproject.toml          # отдельный, чтобы не дёргать корневой
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py             # FastAPI app factory + CORS
│   │   ├── config.py           # pydantic-settings из env
│   │   ├── deps.py             # DI: provider, agent, stores, sessions
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   ├── chat.py         # /api/chat/* endpoints (run, stream, resume)
│   │   │   ├── sessions.py     # /api/sessions/*
│   │   │   ├── tools.py        # /api/tools
│   │   │   ├── providers.py    # /api/providers
│   │   │   └── health.py       # /api/health
│   │   ├── schemas/
│   │   │   ├── chat.py         # pydantic request/response модели
│   │   │   └── sessions.py
│   │   └── services/
│   │       ├── agent_factory.py  # обёртка над sdk.create_agent
│   │       ├── session_store.py  # JSONL сессии + индекс
│   │       └── tool_surface.py   # маппинг человеко-понятных пресетов tools
│   └── tests/
│       ├── conftest.py
│       ├── test_chat_run.py
│       ├── test_chat_stream.py
│       ├── test_resume.py
│       └── test_sessions.py
└── frontend/
    ├── package.json
    ├── vite.config.ts
    ├── tsconfig.json
    ├── index.html
    ├── tailwind.config.ts
    ├── postcss.config.js
    ├── public/
    │   └── favicon.svg
    └── src/
        ├── main.tsx
        ├── App.tsx
        ├── lib/
        │   ├── api.ts          # fetch wrappers
        │   ├── sse.ts          # @microsoft/fetch-event-source wrapper
        │   ├── events.ts       # типы для RunStreamEvent + сужения
        │   └── markdown.ts     # настройка react-markdown + shiki
        ├── store/
        │   ├── chatStore.ts    # zustand: messages, run state, interrupts
        │   ├── sessionStore.ts # zustand: список и текущая сессия
        │   └── settingsStore.ts# провайдер, модель, инструменты, тема
        ├── components/
        │   ├── layout/
        │   │   ├── AppShell.tsx
        │   │   ├── Sidebar.tsx
        │   │   └── Header.tsx
        │   ├── chat/
        │   │   ├── ChatView.tsx
        │   │   ├── MessageList.tsx
        │   │   ├── MessageBubble.tsx       # markdown + code highlight
        │   │   ├── AssistantStreaming.tsx  # стрим с курсором
        │   │   ├── ToolCallCard.tsx        # развёртываемый JSON
        │   │   ├── InterruptCard.tsx       # approve/reject/edit/cancel
        │   │   ├── PlanningCard.tsx        # planning events
        │   │   ├── TokenUsageBar.tsx
        │   │   ├── ComposerInput.tsx       # textarea + send / cmd+enter
        │   │   └── EmptyState.tsx
        │   ├── settings/
        │   │   ├── ProviderPicker.tsx
        │   │   ├── ModelPicker.tsx
        │   │   ├── ToolsPicker.tsx
        │   │   └── LimitsForm.tsx
        │   ├── sessions/
        │   │   ├── SessionList.tsx
        │   │   ├── SessionItem.tsx
        │   │   └── NewSessionButton.tsx
        │   └── ui/             # shadcn/ui примитивы (Button, Dialog, ...)
        ├── pages/
        │   ├── ChatPage.tsx
        │   └── ReplayPage.tsx
        ├── hooks/
        │   ├── useRunStream.ts # подписка на /api/chat/runs/{id}/stream
        │   ├── useSessions.ts
        │   └── useTools.ts
        ├── styles/
        │   └── globals.css     # Tailwind + CSS variables (light/dark)
        └── types/
            └── api.ts
```

---

## 4. Backend (FastAPI)

### 4.1. Зависимости

- `fastapi >= 0.115`
- `uvicorn[standard]`
- `pydantic >= 2.7` (уже в основном пакете)
- `pydantic-settings`
- `agent-driver` (editable install из корня: `pip install -e ..`)
- dev: `pytest`, `pytest-asyncio`, `httpx`, `ruff`/`black`/`isort`

### 4.2. Конфигурация (`backend/app/config.py`)

Из env (см. `.env.example`):

```
APP_HOST=127.0.0.1
APP_PORT=8000
APP_CORS_ORIGINS=http://localhost:5173

AGENT_DRIVER_PROVIDER=fake             # fake | openrouter | vllm | ollama
AGENT_DRIVER_MODEL=...
AGENT_DRIVER_BASE_URL=...
AGENT_DRIVER_API_KEY=...
AGENT_DRIVER_RUNTIME_STORE_KIND=sqlite
AGENT_DRIVER_SQLITE_PATH=./.agent-driver/chat-demo.db

CHAT_DEMO_TOOL_PRESET=safe             # off | safe | dev | all
CHAT_DEMO_MAX_STEPS=24
CHAT_DEMO_MAX_TOOL_CALLS=12
CHAT_DEMO_DEADLINE_SECONDS=180
CHAT_DEMO_STREAM_POLL_INTERVAL_MS=20
CHAT_DEMO_SESSION_DIR=./.agent-driver/sessions
```

Уже существующие переменные библиотеки используем "как есть", не плодим
синонимов.

### 4.3. Эндпоинты

| Метод | Путь | Описание |
| ----- | ---- | -------- |
| `POST` | `/api/chat/runs` | Создать run для session, вернуть `{ run_id, session_id, thread_id }`. Body: `{ session_id?: str, message: str, tool_preset?: str, model?: str, provider?: str }`. |
| `GET`  | `/api/chat/runs/{run_id}/stream` | SSE-стрим. Заголовок `Last-Event-ID` поддерживается для reconnect через `sse_event_stream(..., last_event_id=...)`. |
| `POST` | `/api/chat/runs/{run_id}/resume` | Body: `{ interrupt_id, action: approve/reject/edit/cancel/clarify, edited_tool_args?, message? }`. Возвращает новый `run_id` или продолжение текущего. |
| `GET`  | `/api/sessions` | Список сессий (id, title, updated_at, last_message_preview). |
| `POST` | `/api/sessions` | Создать пустую сессию. Body: `{ title?: str }`. |
| `GET`  | `/api/sessions/{session_id}` | Полная сессия: messages + run_ids + interrupts state. |
| `GET`  | `/api/sessions/{session_id}/replay?run_id=...` | Список `RunStreamEvent` для run (через `event_log.list_for_run`). |
| `DELETE` | `/api/sessions/{session_id}` | Удалить сессию. |
| `GET`  | `/api/tools` | Список tool manifests текущего пресета (name, description, risk, side_effect). |
| `GET`  | `/api/providers` | Доступные провайдеры + дефолтная модель/base_url. |
| `GET`  | `/api/health` | `agent.runner.deps.provider.healthcheck()` + статус store/runtime. |

### 4.4. Контракты

Запрос на новый run:

```jsonc
POST /api/chat/runs
{
  "session_id": "sess_abc123",     // если опущен — создаём новую
  "message": "Привет!",
  "provider": "openrouter",         // optional override
  "model": "openai/gpt-4o-mini",    // optional
  "tool_preset": "safe",            // off | safe | dev | all
  "max_steps": 24,                  // optional
  "stream": true                    // по умолчанию true для UI
}
```

SSE-фрейм один-в-один как у `agent_driver.adapters.sse.render_sse_line`:

```
event: token_delta
id: run_xxx:42
data: {"schema_version":"1.0","stream_id":"run_xxx:42","run_id":"run_xxx",
       "attempt_id":"att_1","seq":42,"event":"token_delta",
       "source":"runtime_event","data":{"text":"Hel"},
       "runtime_event_id":"evt_...","created_at":"2026-05-20T..."}
```

UI парсит `event` и `data` — события маршрутизируются по
`RuntimeEventType` (`run_started`, `llm_call_started`, `token_delta`,
`tool_call_started`, `tool_call_completed`, `interrupt_requested`,
`run_paused`, `run_completed`, `run_failed`, `run_cancelled` и т.д.).

### 4.5. Сессии

Минимально — JSONL-файл на сессию в `CHAT_DEMO_SESSION_DIR`. Формат уже
есть в `agent_driver.cli.sessions.SessionStore` — переиспользуем напрямую
(`from agent_driver.cli.sessions import SessionStore`), не плодим
дубликат.

### 4.6. Tool presets

| Preset | Состав |
| ------ | ------ |
| `off`  | `ToolSet.none()` |
| `safe` | filesystem-read, web pack, planning, tasking (read-only) |
| `dev`  | safe + filesystem-write, bash (ограниченный) — требует подтверждения |
| `all`  | `ToolSet.all()`, max_risk=HIGH, allow_dangerous_tools=true |

`tool_preset` приходит из UI; на бекенде маппится в `ToolSet` и
`max_tool_risk`. Манифесты для `/api/tools` берутся из
`agent.runner.config.tool_registry.list_manifests()`.

### 4.7. Безопасность

- CORS только на `APP_CORS_ORIGINS`;
- API-ключи (`AGENT_DRIVER_API_KEY`) НИКОГДА не уходят на фронт;
- `.env` уже в корневом `.gitignore`;
- ответы `/api/providers` возвращают только `name` / `model` / `base_url`,
  но не сами секреты.

---

## 5. Frontend (React + Vite + TS)

### 5.1. Tech stack (best-practice 2026)

| Слой | Выбор | Почему |
| ---- | ----- | ------ |
| Bundler | **Vite** | стандарт de-facto, быстрый HMR |
| Язык | **TypeScript (strict)** | типы для всех `RunStreamEvent` |
| UI Kit | **shadcn/ui + Radix UI** | копируемые в репозиторий компоненты, A11y, тёмная тема из коробки |
| Стили | **Tailwind CSS v4** | utility-first, темы через CSS variables |
| Состояние | **Zustand** (легко) + **TanStack Query** (server state) | без redux-boilerplate |
| SSE | **@microsoft/fetch-event-source** | поддержка `Last-Event-ID`, retry, `POST`-варианты, не падает в фоне как нативный `EventSource` |
| Markdown | **react-markdown + remark-gfm + rehype-shiki** | таблицы, чек-листы, code highlighting под темы редактора |
| Иконки | **lucide-react** | подходит к shadcn, лёгкие |
| Анимации | **framer-motion** | плавные появления токенов и карточек tool calls |
| Виртуализация | **@tanstack/react-virtual** | длинные сессии не лагают |
| Утилиты | **clsx + tailwind-merge**, **date-fns**, **nanoid** | стандарт |
| Тесты | **vitest + @testing-library/react** | быстрее jest, ts-first |
| Lint/format | **eslint flat config + prettier** | стандарт |
| Doc / DX | **Storybook 8** (опционально, на 2-м спринте) | для UI Kit и QA по компонентам |

### 5.2. UI/UX ориентиры

Берём приёмы у OpenRouter (скрин из задачи), Claude.ai и ChatGPT:

- **двухколоночный layout**: слева Sidebar c сессиями + кнопка `New chat`,
  справа основной view;
- **Header** с переключателем модели/провайдера/tool preset (компактный,
  как у OpenRouter сверху);
- **Чат**:
  - сообщения юзера — короткие plain-text баблы справа (или просто
    цитата сверху, как в Claude);
  - сообщения ассистента — **без баббла**, во всю ширину, чтобы код и
    таблицы дышали;
  - стриминг токенов — мигающий курсор `▍` в конце;
  - **Tool call cards** — `ToolCallCard.tsx`:
    - сворачиваемая карточка `▸ ● Bash(ls -la)` (визуально как в Claude CLI);
    - badge с risk/side-effect/duration;
    - в раскрытом виде — args (JSON-tree) и preview результата;
  - **Interrupt cards** — `InterruptCard.tsx` (для HITL):
    - выделенная карточка с заголовком "Требуется подтверждение",
      описание `proposed_action`, кнопки `Approve` / `Edit` / `Reject` /
      `Cancel` + поле `Clarify…`;
  - **Planning cards** — `PlanningCard.tsx` для `channel=planning`
    событий (чек-листы);
  - **Token usage bar** внизу — компактно: `↑ 1.2k  ↓ 0.8k · ctx 60%`;
- **Composer**:
  - multi-line textarea с автогрузеличением высоты;
  - `⌘/Ctrl + Enter` — отправить, `Enter` — новая строка (как Claude);
  - в подсказке слева — выбор tool preset (chip);
  - `/` — открывает палитру команд (`/clear`, `/reset`, `/replay …`,
    `/export`);
  - кнопка `Stop` (отмена run-а) во время стрима;
- **Replay**: отдельная страница `/replay/:run_id`, рисует тот же
  компонент `MessageList`, но в read-only режиме и с тайм-таймстампами;
- **Theming**: тёмная по умолчанию (как на скрине), переключатель в header;
- **Mobile**: Sidebar превращается в drawer, composer прилипает снизу.

### 5.3. Доступность (A11y)

- все Radix-компоненты с ARIA из коробки;
- `Tab` навигация по карточкам tool calls и interrupt;
- focus ring видимый;
- `prefers-reduced-motion` отключает framer анимации.

### 5.4. Маршрутизация

`react-router-dom`:
- `/` → редирект на последнюю сессию или `/sessions/new`;
- `/sessions/:id` → ChatPage;
- `/sessions/:id/replay/:run_id` → ReplayPage;
- `/settings` → SettingsPage (провайдеры/инструменты).

### 5.5. Обработка SSE

`useRunStream(runId)` hook:

1. Открывает `fetchEventSource(/api/chat/runs/{runId}/stream)` с заголовком
   `Last-Event-ID` если есть кешированный seq.
2. Парсит `event` + `data`, диспатчит в `chatStore`:
   - `token_delta` → `appendDelta(text)` на текущее ассистентское сообщение;
   - `tool_call_started` → создать карточку с `status="running"`;
   - `tool_call_completed` → обновить карточку с `status`, `duration_ms`,
     `result_preview`;
   - `interrupt_requested` → создать `InterruptCard` + поставить
     `chatStore.pendingInterrupt`;
   - `run_completed/failed/cancelled` → закрыть стрим, обновить usage.
3. Сохраняет `seq` последнего обработанного события для reconnect.
4. На ошибке сети — экспоненциальный backoff (есть в библиотеке).

---

## 6. DevOps / запуск

### 6.1. Локально

```bash
# из examples/chat-demo
cp .env.example .env

# backend
cd backend && python -m venv .venv && . .venv/bin/activate
pip install -e ../../..[cli]  # ставит agent-driver editable
pip install -e .
uvicorn app.main:app --reload --port 8000

# frontend (в другом терминале)
cd frontend && pnpm install && pnpm dev   # http://localhost:5173
```

### 6.2. Makefile

```
make install       # ставит backend + frontend deps
make dev           # одной командой поднимает backend + frontend (concurrently)
make test          # pytest backend + vitest frontend
make lint          # ruff + eslint
make build         # build frontend → backend/app/static (опционально)
make docker-up     # docker-compose up
```

### 6.3. docker-compose.yml

Два сервиса: `backend` (python:3.12-slim, uvicorn) + `frontend` (node:20,
vite dev или nginx со сборкой). Volume на `.agent-driver/` для sqlite.

### 6.4. Single-binary режим (для демо)

Опция: `make build` собирает фронт в `dist/` и кладёт в
`backend/app/static/`, FastAPI отдаёт через `StaticFiles`. Один процесс,
один порт, удобно для скриншот-демо.

---

## 7. Тестирование

### 7.1. Backend

- `test_chat_run.py`: POST /api/chat/runs c `FakeProvider` (через
  `AGENT_DRIVER_PROVIDER=fake`) — проверяет 200 и валидный `run_id`;
- `test_chat_stream.py`: SSE-стрим возвращает корректные SSE-фреймы и
  завершается `event: run_completed`;
- `test_resume.py`: planted interrupt → POST /resume approve → run
  завершается;
- `test_sessions.py`: CRUD сессий + persist между перезапусками
  (через sqlite store).

### 7.2. Frontend

- `vitest`:
  - `chatStore` редьюсеры (token_delta, tool_call_*, interrupt_*);
  - `events.ts` нарративные сужения;
  - компонентные `MessageBubble`, `ToolCallCard`, `InterruptCard` (RTL +
    snapshot light).

### 7.3. E2E (опционально)

`playwright`: один happy-path сценарий с `FakeProvider` —
"открыть приложение → ввести сообщение → дождаться стрима → подтвердить
интеррапт → дождаться completed".

### 7.4. Acceptance (ручная QA)

Чек-лист в `README.md`:

- [ ] стрим токенов рисуется буква-в-букву;
- [ ] markdown + код подсвечивается;
- [ ] tool call карточка появляется и схлопывается;
- [ ] interrupt card даёт approve/reject/edit/cancel/clarify;
- [ ] перезапуск бекенда не теряет сессии;
- [ ] reconnect SSE с `Last-Event-ID` догоняет пропущенные seq;
- [ ] /api/providers и /api/tools отдают непустой список;
- [ ] переключение провайдера `fake → openrouter` работает без перезагрузки.

---

## 8. Дорожная карта (по спринтам ≈ 1 день каждый)

### Sprint 0 — Bootstrap (0.5d)

- создать `examples/chat-demo/{backend,frontend}/` со стартерами;
- `pyproject.toml` для backend, `package.json` для frontend;
- `Makefile`, `.env.example`, `docker-compose.yml`, `README.md`;
- CI hook (если есть): запустить `pytest backend/tests` + `vitest run`.

### Sprint 1 — Backend MVP (1d)

- `app/main.py` + DI (`deps.py`, `agent_factory.py`);
- `/api/health`, `/api/providers`, `/api/tools`;
- `/api/chat/runs` (POST) + `/api/chat/runs/{id}/stream` (SSE,
  `FakeProvider`);
- pytest на endpoints + sse.

### Sprint 2 — Frontend MVP (1d)

- Vite + Tailwind + shadcn/ui init;
- `AppShell`, `ChatPage`, `MessageList`, `MessageBubble`,
  `ComposerInput`, `AssistantStreaming`;
- `useRunStream` hook + zustand `chatStore`;
- markdown + shiki + dark theme;
- happy path: ввести → отправить → увидеть стрим от `FakeProvider`.

### Sprint 3 — Tools UI (1d)

- `/api/tools` + `ToolsPicker`;
- `ToolCallCard` (running/done states, expandable args/result);
- preset `safe` подключён, web-pack тест с реальным OpenRouter (опционально).

### Sprint 4 — Sessions (1d)

- `/api/sessions` + `/api/sessions/{id}` (`SessionStore`);
- Sidebar (`SessionList`, `NewSessionButton`);
- persist `chatStore` через session API;
- роутинг `/sessions/:id`.

### Sprint 5 — HITL (1d)

- `interrupt_requested` парсинг в `useRunStream`;
- `InterruptCard` с кнопками approve/edit/reject/cancel/clarify;
- `/api/chat/runs/{id}/resume` + e2e тест на planted interrupt.

### Sprint 6 — Replay + Polish (1d)

- `/api/sessions/{id}/replay?run_id=…`;
- `ReplayPage` (read-only `MessageList`, тайминги, фильтр по `event_type`);
- token usage bar, кнопка Stop, /-палитра команд;
- финальная стилизация под скриншот (паддинги, типографика, иконки).

### Sprint 7 — Готовность к демо (0.5d)

- `make build` (фронт в static FastAPI);
- скриншот в `README.md`;
- запись короткого видео-демо (опционально);
- acceptance чек-лист пройден.

**ИТОГО:** ~6 рабочих дней для одного разработчика.

---

## 9. Definition of Done (для первой итерации)

- [ ] `examples/chat-demo/README.md` объясняет 3 команды запуска;
- [ ] локально (`make dev`) поднимается полный стек, `FakeProvider`
      отдаёт стрим в UI;
- [ ] `openrouter` через `.env` работает без правки кода;
- [ ] `pytest examples/chat-demo/backend/tests` зелёный;
- [ ] `pnpm test --run` зелёный;
- [ ] acceptance чек-лист в README выполнен;
- [ ] roadmap `docs/roadmap.md` Phase 10 → "Add example apps:
      FastAPI chat backend using SSE adapter" помечен реализованным
      ссылкой на `examples/chat-demo/`.

---

## 10. Out of scope (для будущих итераций)

- Claude-style **artifacts**: правый сайдбар с интерактивными артефактами
  (HTML preview, диаграммы, runnable code) — отдельный спринт;
- мультиюзер + auth (GitHub OAuth / OIDC);
- встроенный **Eval-runner UI**: запуск `agent_driver.evals` дата-сетов
  и сравнение отчётов в UI;
- **Sub-agent tree view**: визуализация `SubagentGroup` (Phase 9
  библиотеки), когда runtime будет публиковать достаточно событий;
- **MCP server picker**: UI для подключения MCP-серверов через
  `mcp_catalog.json` (Phase 10 backlog библиотеки);
- **Trace inspector**: timeline всех событий с фильтрами/поиском
  (вместо текущей таб-view с заголовками лейб-классов);
- WebSocket-транспорт (только когда SSE упрётся в multi-user).

---

## 11. Открытые вопросы (нужны решения до старта)

1. **package manager на фронте** — `pnpm` (рекомендую) или `npm`?
2. **shadcn/ui** копировать в репо или взять более готовый kit (e.g.
   tremor / aceternity)?
3. **тесты backend** — `httpx.AsyncClient` поверх `app` или `TestClient`?
4. **sqlite-путь** — кладём в `.agent-driver/chat-demo.db` (уже в
   `.gitignore`) или в `examples/chat-demo/.data/`?
5. **multitenant сессии** — пока single-user; если в демо нужно
   изолировать треды по `?user=`, добавляем `user_id` в `SessionStore`
   key — но это +0.5d.
6. **тёмная / светлая по умолчанию** — на скрине тёмная; делаем тёмную
   default + переключатель.

---

## 12. Связь с library roadmap

Этот демо закрывает следующие пункты `docs/roadmap.md`:

- Phase 10 → "Add example apps: …FastAPI chat backend using SSE adapter";
- Phase 10.5 → product parity backlog (provider/tools/sessions/replay
  через UI вместо CLI);
- частично Phase 4 (HITL UX, теперь видимо глазами);
- частично Phase 5 (replay view как визуализация event log).

Любые расхождения "фронт хочет, а runtime не отдаёт" фиксируются как
issues в `docs/refactor/` или в backlog Phase 10.6.
