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

Доступные packs:

- `filesystem_read`: `read_file`, `glob_search`, `grep_search`
- `filesystem_write`: `file_write`, `file_edit`, `notebook_edit`
- `web`: `web_fetch`, `web_search`
- `shell`: `bash`
- `planning`: `planning_state_update`, `todo_write`, `ask_user_question`
- `tasking`: `task_create`, `task_get`, `task_list`, `task_update`, `task_output`
- `mcp`: `mcp_tool`, `mcp_list_resources`, `mcp_read_resource`

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

`file_write` записывает UTF-8 текст в файл в режиме `overwrite` или `append`.
Полезен для создания новых файлов и простых генераций. Это reversible write:
путь проходит проверку writable path, результат проверяется по размеру, а
создание родительской директории возможно только при явном `create_parent`.

`file_edit` заменяет ожидаемый фрагмент текста в UTF-8 файле. Полезен для
точечных правок, когда можно указать стабильный `old_text`. Handler проверяет
точное количество occurrences перед заменой, чтобы не сделать случайную широкую
правку.

`notebook_edit` редактирует один cell в `.ipynb` или вставляет новый. Полезен для
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

## Web

`web_fetch` загружает текстовое содержимое HTTP(S) URL с лимитами по timeout,
байтам и символам. Полезен для чтения внешней документации и API страниц.
Handler запрещает private/localhost targets по умолчанию, принимает только
текстовые content types и умеет возвращать raw/text/markdown-like extraction.

`web_search` выполняет публичный web search и возвращает нормализованные
результаты `title`, `url`, `snippet`. Полезен, когда нужна актуальная внешняя
информация. Для тестов и offline сценариев поддерживает `mock_results`, иначе
использует HTML-страницу DuckDuckGo и простой parser результатов.

## Planning And HITL

`planning_state_update` возвращает нормализованный payload для обновления
planning state между turn-ами. Полезен runtime-интеграциям, которым нужно
сохранять текущий план, todo или режим работы. Слияние с состоянием выполняется
детерминированными helpers из context layer.

`todo_write` применяет структурированное обновление todo-листа. Полезен для
многошаговых задач, где агент должен явно вести прогресс. Handler валидирует
id/content/status и не допускает больше одного `in_progress` пункта.

`ask_user_question` формирует structured clarification interrupt с prompt,
choices и `allow_multiple`. Полезен, когда выполнение заблокировано выбором
пользователя. Инструмент не вызывает внешний side effect сам по себе, а возвращает
payload с reason `clarification_required` для runtime/UI.

`enter_plan_mode` и `exit_plan_mode` меняют metadata planning state между
режимами `plan` и `agent`. Они полезны, когда runtime поддерживает явное
разделение проектирования и исполнения.

## Tasking

`task_create`, `task_get`, `task_list`, `task_update` и `task_output` ведут
session-local task store для долгих или фоновых работ. Они полезны для
мониторинга jobs, хранения статуса и bounded output chunks. Store реализован
in-memory структурой с lock, стабильными task id, статусами `running`,
`completed`, `failed`, `timed_out`, `killed` и preview-лимитами для вывода.

Read-only операции (`task_get`, `task_list`) имеют низкий риск и не требуют
approval. Операции создания, обновления и добавления output являются reversible
write и проходят policy approval.

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

## Skills, Tool Discovery And Briefs

`skill_tool` ищет `SKILL.md` под базовой директорией и возвращает путь,
relative path, provenance и trust classification. Полезен для discovery
локальных agent skills. Внутри идет обход `base.rglob("SKILL.md")`, опциональная
фильтрация hidden paths и проверка trusted roots.

`tool_search` ищет зарегистрированные manifests по имени/описанию, risk и
side effect. Полезен для discovery доступной tool surface прямо во время run.
Handler строится вокруг текущего `ToolRegistry`, поэтому видит фактически
зарегистрированные инструменты и может опционально вернуть schemas.

`brief_tool` создает runtime brief payload: message, channel и optional artifact
attachments. Полезен для передачи пользователю или UI короткого статуса с
ссылками на artifacts. Handler нормализует attachments в `ContextArtifactRef`,
обрезает слишком длинное сообщение и добавляет timestamp.

## Risk And Approval

Read-only и no-side-effect инструменты обычно имеют `ApprovalMode.NEVER`.
Инструменты с записью, shell или внешними действиями чаще используют
`ApprovalMode.ON_POLICY_MATCH`. Фактическая блокировка и approval зависят от
`GovernedToolExecutor`, `GuardrailPipeline` и policy layer, а не только от
manifest: manifest описывает намерение и ограничения, executor применяет их в
конкретном run.
