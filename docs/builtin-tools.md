# Built-in Tools Overview

Этот документ кратко описывает текущую встроенную поверхность инструментов
`agent-driver`: что инструменты позволяют делать, когда они полезны и как
устроены внутри runtime.

## Как Они Устроены

Каждый инструмент регистрируется в `ToolRegistry` парой `ToolManifest` +
async-handler. Manifest задает имя, описание, JSON schema аргументов, уровень
риска, класс side effect, режим approval, timeout и бюджет вывода. Handler
валидирует аргументы, выполняет действие и возвращает JSON payload с кратким
`summary` и структурированными полями результата.

Встроенный набор подключается через `register_builtin_tools(...)`. Для сужения
модельной и исполняемой поверхности используется `ToolSet`: можно выбрать
конкретные имена, ограничить максимальный риск, side effects, профиль агента
или взять готовые packs.

Статусы реализации, которые используются в manifest metadata и документации:

- `native`: полноценная текущая реализация внутри `agent-driver`.
- `session_local_state`: локальное in-memory состояние в рамках текущего runtime.
- `request_envelope`: инструмент формирует intent/request payload без внешнего
  исполнения.
- `platform_gated_native`: нативная реализация, но доступность зависит от
  платформы/бинарей.

Доступные packs:

- `filesystem_read`: `read_file`, `glob_search`, `grep_search`
- `filesystem_write`: `file_write`, `file_edit`, `file_patch`, `notebook_edit`
- `artifacts`: `artifact_list`, `artifact_read`, `artifact_preview`
- `web`: `web_fetch`, `web_search`
- `shell`: `bash`, `powershell_tool`
- `python_exec`: `python`
- `code_intelligence`: `lsp_tool`
- `planning_progress`: `planning_state_update`, `todo_write`,
  `ask_user_question`
- `planning_approval`: `enter_plan_mode`, `exit_plan_mode_v2`
- `planning`: `planning_state_update`, `todo_write`, `ask_user_question`,
  `enter_plan_mode`, `exit_plan_mode_v2`
- `tasking`: `task_create`, `task_get`, `task_list`, `task_update`, `task_output`,
  `task_stop_tool`, `monitor_tool`, `sleep_tool`
- `mcp`: `mcp_tool`, `mcp_list_resources`, `mcp_read_resource`, `mcp_auth`
- `discovery`: `skill_tool`, `tool_search`, `brief_tool`, `agent_tool`,
  `send_message_tool`, `list_peers_tool`, `team_create_tool`, `team_delete_tool`,
  `team_get_tool`, `team_list_tool`
- `worktree`: `enter_worktree_tool`, `exit_worktree_tool`
- `automation`: `workflow_tool`, `cron_create_tool`, `cron_delete_tool`,
  `cron_list_tool`, `remote_trigger_tool`, `subscribe_pr_tool`,
  `push_notification_tool`, `send_user_file_tool`

## Filesystem

`read_file` читает UTF-8 файл по абсолютному пути, умеет возвращать срез по
строкам и нумерует строки. Полезен для точечного чтения кода и документации без
загрузки больших файлов целиком. Внутри использует проверку размера, нормализацию
пути и детерминированный line slicing.

`glob_search` ищет пути по glob-паттерну под базовой директорией, уважает
ignore-паттерны и ограничивает глубину обхода. Полезен для поиска файлов по
имени или расширению. Работает через обход `Path.rglob("*")`, фильтрацию
относительных путей и лимиты на количество результатов.

`grep_search` ищет содержимое файлов Python-regex паттерном и возвращает
file/line/preview совпадения. Полезен для поиска символов, вызовов и текстовых
паттернов в кодовой базе. Внутри читает только текстовые UTF-8 файлы, учитывает
ignore-паттерны и ограничивает число совпадений, файлов и длину preview.

`file_write` (`native`) записывает UTF-8 текст в файл в режиме `overwrite` или
`append`.
Полезен для создания новых файлов и простых генераций. Это reversible write:
путь проходит проверку writable path, результат проверяется по размеру, а
создание родительской директории возможно только при явном `create_parent`.

`file_edit` (`native`) заменяет ожидаемый фрагмент текста в UTF-8 файле. Полезен для
точечных правок, когда можно указать стабильный `old_text`. Handler проверяет
точное количество occurrences перед заменой, чтобы не сделать случайную широкую
правку.

`file_patch` (`native`) применяет несколько точных замен к одному UTF-8 файлу за
один вызов. Полезен для deep research и длинных отчетов, где нужно обновить
несколько секций без повторной генерации всего документа. Каждая операция
проверяет ожидаемое число вхождений и возвращает суммарные replacements.

`notebook_edit` (`native`) редактирует один cell в `.ipynb` или вставляет новый. Полезен для
детерминированных правок ноутбуков без ручной работы с JSON. Инструмент парсит
notebook как JSON, проверяет индекс cell, тип cell и требует, чтобы `old_text`
встречался ровно один раз при замене.

## Shell

`bash` выполняет shell-команды с timeout и ограниченным stdout/stderr. Полезен
для read-only проверок: `ls`, `pwd`, `rg`, `git status/log/show/diff`, `pytest` и
похожих команд из allowlist. Несмотря на read-only intent, manifest помечен как
high risk / irreversible write и требует policy approval, потому что shell
потенциально опасен. Внутри команда разбивается на сегменты, блокируются
редиректы, `;`, destructive keywords и write-like git subcommands.

`powershell_tool` (`platform_gated_native`) дает policy-compatible sibling для shell на базе `pwsh`.
На Linux без `pwsh` возвращает явный unavailable error, а при наличии бинаря
выполняет bounded command с timeout и capped stdout/stderr.

## Web

`web_fetch` загружает текстовое содержимое HTTP(S) URL с лимитами по timeout,
байтам и символам. Полезен для чтения внешней документации и API страниц.
Handler запрещает private/localhost targets по умолчанию, принимает только
текстовые content types и умеет возвращать raw/text/markdown-like extraction.

`web_search` выполняет публичный web search и возвращает нормализованные
результаты `title`, `url`, `snippet`. Полезен, когда нужна актуальная внешняя
информация. Для тестов и offline сценариев поддерживает `mock_results`, иначе
использует HTML-страницу DuckDuckGo и простой parser результатов.

## Code Intelligence

`lsp_tool` предоставляет lightweight read-only операции `symbols`,
`definitions`, `references` без управления долгоживущими language servers.
Инструмент полезен как промежуточный этап до полноценного LSP process manager:
для Python/текстовых файлов возвращает детерминированный symbol/index lookup.

## Planning And HITL

`planning_state_update` возвращает нормализованный payload для обновления
planning state между turn-ами. Полезен runtime-интеграциям, которым нужно
сохранять текущий план, todo или режим работы. Слияние с состоянием выполняется
детерминированными helpers из context layer.

`todo_write` применяет структурированное обновление todo-листа. Полезен для
многошаговых задач, где агент должен явно вести прогресс. Handler валидирует
id/content/status и не допускает больше одного `in_progress` пункта.

### Chat TUI plan panel

В интерактивном `agent-driver chat` (rich mode):

- после `todo_write` / `planning_state_update` показывается панель **plan** с чеклистом (✓ / ■ / □);
- прогресс дублируется в footer (`plan 2/5 · current step`);
- план сохраняется в session между turn'ами и передаётся в следующий run как `planning_state_seed`;
- команда `/plan` печатает текущий чеклист без нового LLM-хода;
- `/clear` и `/reset` сбрасывают план и панель.

Промпт chat-mode просит модель при запросе «составь план» сначала вызвать `todo_write`; чеклист виден только в панели plan — в prose не дублировать полный список. Статусы обновлять через `merge=true` сразу после каждого шага (`completed` → следующий `in_progress`). Runtime периодически напоминает модели о незакрытых шагах после содержательных tools (`web_fetch`, поиск, чтение файлов).

`ask_user_question` формирует structured clarification interrupt. Старый формат
`prompt` + `choices` сохранен для совместимости, а текущий предпочтительный
формат добавляет `questions`: 1-4 коротких вопроса с уникальными headers,
optional preview и 2-4 уникальными вариантами ответа. Полезен только когда
выполнение реально заблокировано пользовательским решением. Инструмент не
является plan approval и не должен использоваться, чтобы избежать выдачи
запрошенного deliverable. Он не вызывает внешний side effect сам по себе, а
возвращает payload с reason `clarification_required` для runtime/UI.

`enter_plan_mode` и `exit_plan_mode_v2` меняют metadata planning state между
режимами `plan` и `agent`. Они полезны, когда runtime поддерживает явное
разделение проектирования и исполнения.
`exit_plan_mode_v2` является canonical public approval-exit tool name;
`exit_plan_mode` остается только legacy trace alias.

## Tasking

`task_create`, `task_get`, `task_list`, `task_update` и `task_output` ведут
session-local task store для долгих или фоновых работ. Они полезны для
мониторинга jobs, хранения статуса и bounded output chunks. Store реализован
in-memory структурой с lock, стабильными task id, статусами `running`,
`completed`, `failed`, `timed_out`, `killed` и preview-лимитами для вывода.

Read-only операции (`task_get`, `task_list`) имеют низкий риск и не требуют
approval. Операции создания, обновления и добавления output являются reversible
write и проходят policy approval.

`task_stop_tool` завершает task переводом в terminal status (`killed`,
`timed_out`, `failed`, `completed`), `monitor_tool` возвращает bounded
monitoring-view по task output, а `sleep_tool` делает bounded wait helper для
детерминированных runtime-пауз.

## MCP

`mcp_tool` вызывает readonly MCP-style tool по паре `server` + `tool_name`.
Полезен как адаптер к внешним tool descriptors. Текущая реализация работает со
статическими demo descriptors и возвращает provenance, description, args schema
и переданные arguments.

`mcp_list_resources` и `mcp_read_resource` перечисляют и читают MCP resources.
Полезны для доступа к документам или данным, опубликованным MCP server-ом.
Внутри используются статические resource descriptors, а чтение ограничивается
`max_chars`.

`mcp_auth` настраивает token или OAuth stub flow для MCP server-а. Полезен для
моделирования authentication state. Инструмент хранит состояние в session-local
dict и помечен как external action с policy approval.

## Skills, Tool Discovery, Briefs And Subagents

`skill_tool` (`native`) ищет `SKILL.md` под базовой директорией и возвращает
metadata-first каталог: `name`, `description`, `when_to_use`, `version`, `tags`,
`allowed_tools`, `context`, `agent`, `paths`, supporting file index, path
provenance и trust classification. Полезен для discovery локальных agent
skills без загрузки полного тела. Внутри используется общий
`agent_driver.skills` parser/registry, опциональная фильтрация hidden paths и
проверка trusted roots.

`skill_view` (`native`) загружает выбранный `SKILL.md` или один supporting file
из директории skill-а. Он возвращает full/trimmed content, skill manifest,
trust/safety warnings и компактный `skill_invocation` record, который runtime
сохраняет в events/metadata для compaction survival.

`tool_search` (`native`) ищет зарегистрированные manifests по имени/описанию, risk и
side effect. Полезен для discovery доступной tool surface прямо во время run.
Handler строится вокруг текущего `ToolRegistry`, поэтому видит фактически
зарегистрированные инструменты и может опционально вернуть schemas.

`brief_tool` (`session_local_state`) создает runtime brief payload: message, channel и optional artifact
attachments. Полезен для передачи пользователю или UI короткого статуса с
ссылками на artifacts. Handler нормализует attachments в `ContextArtifactRef`,
обрезает слишком длинное сообщение и добавляет timestamp.

`agent_tool` (`request_envelope`) создает структурированный payload для запроса на запуск subagent:
task, description, execution mode, idempotency key и metadata. Полезен как
переходная форма между tool-calling и Phase 9 orchestration, где runtime может
принять этот payload и записать child-run/group rows.

`send_message_tool` (`session_local_state`) создает session-local message event для teammate/subagent
взаимодействия: recipient, thread, channel, message и metadata. Полезен как
переходный адаптер для collaboration workflows до появления полноценных
teammate sessions и распределенной маршрутизации сообщений.

`list_peers_tool` (`session_local_state`) возвращает session-local directory peers с фильтрами по status
и capability. Полезен для выбора адресата перед `send_message_tool` и для
контролируемого discovery доступных "teammate" ролей до полной оркестрации.

`team_create_tool` и `team_delete_tool` (`session_local_state`) управляют session-local team registry:
создают и удаляют team rows с members/purpose/metadata. Полезны для явной
группировки peer-ов перед fanout/collaboration шагами и остаются reversible
локальным состоянием до внедрения полноценных teammate sessions.

`team_get_tool` и `team_list_tool` (`session_local_state`) дают read-only доступ к этому team registry:
точечная загрузка по `team_id` и фильтрация по member. Полезны для
детерминированных lookup сценариев перед отправкой сообщений или fanout-логикой.

## Worktree And Automation

`enter_worktree_tool` и `exit_worktree_tool` (`request_envelope`) формируют high-risk request
envelopes для будущего worktree executor path. В текущей версии это intent
payloads с явной provenance и approval-critical risk profile.

`workflow_tool`, cron trio (`cron_create_tool`, `cron_delete_tool`,
`cron_list_tool`), `remote_trigger_tool`, `subscribe_pr_tool`,
`push_notification_tool`, `send_user_file_tool` (`request_envelope` /
`session_local_state` в зависимости от инструмента) пока работают как local intent
adapters: они не зовут внешние сервисы напрямую, а возвращают нормализованные
event payloads для последующей продуктовой интеграции.

В текущей реализации: `workflow_tool`, `remote_trigger_tool`,
`push_notification_tool`, `send_user_file_tool` помечены как `request_envelope`,
а cron и PR subscription row-хранилище — как `session_local_state`.

## Risk And Approval

Read-only и no-side-effect инструменты обычно имеют `ApprovalMode.NEVER`.
Инструменты с записью, shell или внешними действиями чаще используют
`ApprovalMode.ON_POLICY_MATCH`. Фактическая блокировка и approval зависят от
`GovernedToolExecutor`, `GuardrailPipeline` и policy layer, а не только от
manifest: manifest описывает намерение и ограничения, executor применяет их в
конкретном run.

## Test Status Snapshot

Текущий базовый статус проверок для встроенных инструментов и runtime lanes:

- offline suite (`uv run pytest tests/ -q` при `AGENT_DRIVER_RUN_LIVE_TESTS=0`) — green;
- OpenRouter live suite (`-m live -k "not ollama"`) — green;
- Postgres live lane (`tests/runtime/test_postgres_store_live.py`) — green;
- Ollama live lane остается optional и зависит от доступности локального
  Ollama endpoint (`http://localhost:11434` по умолчанию).
