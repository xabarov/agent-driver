# OpenClaude/Hermes Improvement Plan

Дата исходного анализа: 2026-05-29.  
Последняя сводка: 2026-05-30.

Цель: улучшать `agent-driver`, беря лучшие практики из
`/home/roman/pyprojects/ML/openclaude` и
`/home/roman/pyprojects/ML/hermes-agent` в трех направлениях:

- force planning: планирование и approval только там, где оно действительно
  защищает пользователя или качество работы;
- steerability: понятное управление агентом во время выполнения;
- subagents: дочерние исполнители, mailbox, синтез и управляемая
  оркестрация без лишнего DAG-шума.

## Principles

- Движемся в духе Python Zen: простота, читаемость и проверяемое поведение
  важнее сложной механики ради механики.
- Если проблему можно надежно решить связкой "модель + prompt + небольшой
  runtime guard", сначала делаем так.
- Сложную оркестрацию добавляем только по явным сигналам из traces/tests:
  повторяемые сбои, невозможность проверить поведение, реальные требования к
  parallelism, durability, human approval или recovery.
- Если находится решение, упрощающее текущую реализацию без потери качества,
  применяем упрощение и фиксируем причину.
- Chat demo держим чистым: это витрина и проверочный стенд, а не второй
  движок. Переиспользуемое поведение уходит в `agent_driver`; в demo остаются
  FastAPI/React wiring, локальные настройки, session UI и сценарии проверки.

## Prompt Assembly Principle

Следующий крупный шаг по prompt/runtime качеству: перейти от одного большого
`react_chat_tool_policy.txt` с блоками вида "если доступна эта тулза..." к
динамической сборке system prompt из короткого ядра и tool-aware фрагментов.

Мотивация:

- Модель должна видеть инструкции только для реально доступных в данном turn
  инструментов. Нет `python` / `web_search` / `agent_tool` в effective tool
  surface - нет соответствующего policy блока в prompt.
- Tool-specific поведение должно жить ближе к tool definition:
  `ToolManifest.description`, schema examples и небольшой prompt fragment для
  cross-tool правил. Это совпадает с Anthropic guidance: description должна
  объяснять, что делает tool, когда ее использовать, параметры и caveats
  ([Anthropic Define tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools)).
- OpenAI также описывает tool calling как передачу модели списка callable
  tools в конкретном request; модель выбирает из данного списка, а не из
  абстрактных возможностей приложения
  ([OpenAI Function calling](https://developers.openai.com/api/docs/guides/function-calling)).
- Большие tool surfaces ухудшают точность выбора и раздувают контекст.
  Anthropic Tool Search прямо решает это on-demand загрузкой релевантных tools
  и отмечает падение selection accuracy при десятках доступных tools
  ([Anthropic Tool search](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)).
- В Hermes полезная практика: сначала вычислить effective tool surface после
  enabled/disabled/filter checks, затем строить tool schema/prompt только для
  реально доступных возможностей. В OpenClaude похожий принцип: tool prompt
  принадлежит конкретному tool, а deferred tools не шумят в начальном prompt.

Рабочее правило: base chat policy отвечает за идентичность, язык, native tool
calling, финальный ответ, clarification и общие recovery boundaries.
Инструкции про web, Python execution, subagents, todo/progress planning и modal
approval planning подключаются отдельными фрагментами только если tool реально
попал в effective request tools после runtime policy filtering.

Ожидаемый результат: prompt становится короче, меньше противоречит
`tool_choice`, проще тестируется и лучше следует Python Zen: вместо сложного DAG
мы делаем ясную модель "доступный инструмент -> его описание -> его небольшой
policy fragment".

Implementation slice:

- [x] Ввести reusable effective tool surface helper, общий для request schema и
  prompt assembly.
- [x] Разрезать chat policy на base policy и tool-aware fragments:
  repository, web, Python execution, subagents, todo checklist, approval
  planning, clarification.
- [x] Подключать Python addendum и todo guidance только когда соответствующий
  tool реально остается в effective tool surface после allow/deny filtering.
- [x] Добавить regression tests: denied/allowed tools исчезают не только из
  `LlmRequest.tools`, но и из system prompt guidance.
- [x] Phoenix/live replay: проверить, что chat demo не показывает модели
  инструкции про disabled web tools и не предлагает недоступные tool paths.
  Latest prompt-surface live replay:
  `prompt-surface-no-web` -> `run_d566d7faef91`;
  `prompt-surface-fetch-only` -> `run_658c229c86d8`.
  `/trace-summary.prompt_surface` confirms no web fragments for no-web and
  only `react_chat_tool_policy_web_fetch.txt` for fetch-only.

## Verification Loop

Рабочий цикл:

`chat scenario -> Phoenix trace -> prompt/runtime hypothesis -> focused patch -> replay`

Сценарий считается успешным только если закрыты нужные слои:

- runtime/provider unit test фиксирует конкретный сбой;
- deterministic Playwright проверяет видимое UI-поведение без зависимости от
  провайдера;
- live Playwright probe проверяет model-dependent поведение на текущем dev
  stack и сверяет `/trace-summary`.

Для planning/search/final-answer/subagent/steering изменений обязательно
добавлять либо provider/runtime regression, либо live probe criterion.

## Chat Rendering / Citations Track

Отдельный план дружелюбного chat UI зафиксирован в
[chat-demo-markdown-citations-plan-2026-05-30.md](chat-demo-markdown-citations-plan-2026-05-30.md).

Короткая цель: Markdown, math, code/Python blocks и citation cards должны
стать штатной частью ответа, а не декоративным слоем поверх raw tool JSON.
В reusable runtime слое нужен нормализованный `SourceEvidence` для `web_fetch`,
`web_search` и ссылок из финального ответа; в demo остается рендеринг,
streaming Markdown и source shelf. Это продолжает общий принцип: chat-demo
чистый, agent-driver хранит переиспользуемый контракт.

## Research Quality Track

Отдельный план повышения качества research-ответов зафиксирован в
[research-quality-improvement-plan-2026-05-31.md](research-quality-improvement-plan-2026-05-31.md).

Короткая цель: research-report задачи должны проходить цикл
`web_search -> web_fetch -> synthesis -> cited final`, а не завершаться после
первичного списка поисковых кандидатов. Это особенно важно после сравнения
`docs/test-examples/fork-join-queues/gpt-5.5`: прямой OpenRouter-ответ был
лучше потому, что дошел до source-backed synthesis, а наш ответ честно
остановился на "нашел кандидаты, но не проверил страницы".

В духе Python Zen сначала делаем легкий `research_depth` contract,
runtime-guard и prompt fragments; сложную исследовательскую DAG-оркестрацию
добавляем только если Phoenix traces покажут, что простого цикла недостаточно.

Глубокое расширение этого направления вынесено в
[research-provider-quality-architecture-plan-2026-05-31.md](research-provider-quality-architecture-plan-2026-05-31.md):
provider/model capabilities, bounded repair, unknown-tool guardrails,
provider failure UX и live Phoenix gates. Это текущая рамка для решений, если
качество research продолжает упираться в слой провайдеров или engine
contracts, а не только в prompt.

## Compaction Notification Track

Отдельный план UX для процесса runtime-суммаризации/compaction зафиксирован в
[chat-demo-compaction-notification-plan-2026-05-30.md](chat-demo-compaction-notification-plan-2026-05-30.md).

Короткая цель: если агент сжимает старый контекст, пользователь должен видеть
спокойное системное уведомление с честным статусом, outcome и debug metadata.
Это не assistant message и не модальное окно; reusable lifecycle/trace contract
должен жить в `agent_driver`, а chat-demo только визуализирует его.

## Closed Work

### Prompt And Runtime Policy

- [x] Переписать `react_chat_tool_policy.txt` в более модульную форму:
  identity, language, tool calling, planning boundaries, research boundaries,
  deliverable boundaries, ask-question boundaries.
- [x] Вынести volatile reminders из base prompt в runtime attachments:
  active task contract, force-final, research-required, existing checklist,
  approved plan state, steering command.
- [x] Сделать `ask_user_question` schema/prompt ближе к OpenClaude:
  1-4 short questions, explicit choices, no plan approval through questions,
  no clarification for deliverables unless blocked.
- [x] Для deliverable задач отделить "progress checklist" от "final answer":
  checklist может существовать, но финал должен закрывать acceptance criteria.

### Scenario Harness

- [x] Добавить run/Phoenix trace extractor для run id:
  LLM calls, tool calls, selected tools, effective `tool_choice`, safe runtime
  reminders, text-form tool calls, progress-only final, missing required tool
  evidence, repeated planning, extra `ask_user_question`.
- [x] Сохранять последний failed live scenario artifact в `/tmp`: screenshot,
  transcript excerpt, trace summary JSON.
- [x] Документировать known bad patterns: repeated planning, progress-only
  final, fake citations without tool evidence, raw JSON in chat, stuck
  clarification.
- [x] Добить минимум 5 live Phoenix-backed scenarios на текущей default model.
  Latest suite includes `clarification-only-when-blocked`,
  `deliverable-no-replan`, `plan-only`, `plan-web-answer`, `research-report`,
  `simple-direct`, and `web-search-final`.

### Core Scenario Set

- [x] `simple-direct`: простой вопрос без planning/tools/interrupts.
- [x] `research-report`: интернет-поиск + финальный ответ с tool evidence.
- [x] `bad-tool-text-recovery`: text-form tool call не становится финалом и не
  зацикливает tools после `tool_choice=none`.
- [x] `web-search-final`: web tools -> финальный ответ.
- [x] `clarification`: clarification UI/resume работает и не ломает plan card.
- [x] `plan-approval`: deterministic approval card/resume path.
- [x] `subagent-final`: deterministic subagent synthesis UI path.
- [x] `deliverable-no-replan`: explicit deliverable не рестартует planning loop.
- [x] `plan-only`: пользователь просит только план; агент показывает checklist
  и не пишет deliverable.
- [x] `clarification-only-when-blocked`: уточнение только для реально
  блокирующего user-owned решения.
- [x] `subagent-synthesis` live: `agent_tool` порождает детей, parent получает
  результаты и выдает синтез.
- [x] `steering-mid-run` live: steering применяется на ближайшей границе,
  отображается в UI и виден в trace.

### Subagents / Steering Next Layer

- [x] Добавить Hermes-style execution blueprint для long research/writing и
  implementation chat tasks: typed phases, worker specs, required handoffs,
  verifier gate, synthesizer final answer, explicit block/retry/error policy.
  Decision: gated/deferred. Не внедряем новый graph слой, потому что текущий
  9-scenario Phoenix/live suite не показывает повторяемую потерю deliverable
  после prompt + runtime guard исправлений. Вернуться к blueprint только при
  новых trace-backed regressions.
- [x] Уточнить steerability semantics в стиле Hermes:
  running user input должен быть `interrupt`, `queue` или
  `steer after next tool result`, с явными UI affordances и trace labels.
  Current mapping: `enqueue_user_message + next` -> `queue_after_next_boundary`,
  `enqueue_user_message + now` -> `steer_at_next_boundary`, `interrupt` ->
  `interrupt_now`; live trace summary exposes `controls.semantic_routes`.
- [x] Добавить узкие policy hook points в стиле Hermes plugins:
  существующий `GovernedToolExecutor` уже поддерживает `pre_tool_use` /
  `post_tool_use`, chaining, context aggregation, timeout isolation and
  `prevent_continuation`; не добавляем второй hook механизм.
- [x] Зафиксировать compaction policy runtime-защитой: сохранять active task
  contract/runtime attachments через recent tail, сохранять leading system
  policy при partial compaction, защищать recent tail и back off при
  неэффективной compression через orchestrator circuit breaker.

### Quality Gate

- [x] Для каждого исправленного failure должен быть provider-level или runtime
  unit regression.
- [x] Перед коммитом запускать `black`, `isort`, focused `pytest`, relevant
  backend/frontend/Playwright checks.
- [x] В конце фазы делать отдельный refactoring/code-quality pass с `pylint`:
  чинить реальные naming/decomposition/typing/import/duplication проблемы, а
  не отключать warnings пачкой.

## Current Architecture Decisions

### Planning

- `todo_write` и `planning_state_update` остаются live-progress planning.
- `enter_plan_mode` / `exit_plan_mode_v2` являются modal approval planning и
  не должны использоваться для обычного research/writing в публичном chat UI.
- Force planning является runtime policy boundary для risky writes,
  side effects и subagent spawn, а не просто prompt-инструкцией.
- Простые factual/research задачи не должны получать modal plan approval.

### Deliverables And Research

- Explicit deliverable turns получают run-level contract:
  финальный ответ важнее нового plan loop.
- Research-required задачи проверяются по trace evidence, а не только по URL в
  финальном тексте.
- Pure research после первого реального `web_search`/`web_fetch` может
  включать force-final, чтобы перейти к синтезу вместо бесконечного поиска.

### OpenRouter/Qwen Compatibility

- Provider может печатать tool call как plain text или только fragment
  arguments при forced `tool_choice`.
- OpenAI-compatible streaming adapter должен:
  - парсить text-form tool calls по накопленному stream text;
  - восстанавливать forced tool call по известному tool name;
  - подавлять text-form tool execution при `tool_choice=none`.

### Observability

- Phoenix lifecycle и OpenTelemetry helpers живут в `agent_driver.observability`.
- Chat-demo использует reusable helpers, но не держит engine policy.
- `/api/chat/runs/{run_id}/trace-summary` является быстрым debug verdict:
  terminal event, LLM calls, tool names, research evidence, planning verdict,
  interrupts, progress-only final, text-form tool calls.
- Live trace summary должен уважать explicit negative constraints:
  "без поиска", "без интернета", "по памяти" отключают research-required даже
  при наличии слов "поиск/интернет" в тексте.
- Runtime/Phoenix spans включают normalized tags:
  `task_contract.kind`, `task_contract.requires_research`,
  `tool_choice.effective`, `force_final_reason`, `continuation_reason`,
  `agent_driver.scenario`.

## Known Bad Patterns

- Repeated approval planning: one run enters and exits modal planning more
  than once instead of executing the accepted plan.
- Progress-only final: the assistant ends with "I will now continue/write..." or
  an equivalent status update, but does not provide the requested answer.
- Fake research evidence: the final text cites or names sources without
  matching `web_search`/`web_fetch` trace evidence when research was required.
- Raw tool JSON in chat: provider emits textual tool-call JSON or
  `<tool_call>` markup instead of native tool calls.
- Stuck clarification: `ask_user_question` pauses the run, but resume/submit
  does not lead to a terminal answer.
- Extra clarification: `ask_user_question` is used for research or deliverable
  tasks where reasonable assumptions should be enough.

## Next Work: Subagent Autonomy And UX

Цель: в chat demo должно быть видно не только что `agent_tool` технически
работает, но и что агент разумно делегирует сложные задачи сам или по прямой
просьбе пользователя, а пользователь может заглянуть в работу дочерних агентов
без чтения сырого JSON.

### Product Behavior

- [x] Уточнить prompt/runtime policy для делегирования:
  - делегировать при явной просьбе пользователя: "поручи субагенту",
    "пусть отдельный агент проверит", "сравни несколькими исполнителями";
  - делегировать для сложных задач, где есть независимые подзадачи:
    research + verification, compare alternatives, draft + critique,
    implementation + review;
  - не делегировать для простых factual вопросов, маленьких переводов,
    коротких ответов и задач, где один проход дешевле и понятнее;
  - child prompt должен быть self-contained, bounded, с явным expected output
    и запретом выдумывать недоступные результаты.
- [x] Упростить UI capability model:
  - Web Search/Web Fetch остаются пользовательскими переключателями;
  - bounded delegation всегда доступно агенту и не выключается в chat demo;
  - модель решает, когда делегировать, по prompt policy и task complexity.
- [x] Добавить model-dependent live сценарии:
  - explicit delegation: пользователь прямо просит поручить часть работы
    субагенту, ожидаем `agent_tool`, child completed, parent synthesis;
  - autonomous delegation: сложная compare/review задача без прямого слова
    "субагент", ожидаем делегирование при обычном web-capable surface;
  - no delegation: простой вопрос при доступном `agent_tool` не должен вызывать
    `agent_tool`;
  - delegation final: parent обязан дать финальный ответ, а не только
    сообщить, что child завершился.

### Runtime / Trace Criteria

- [x] Расширить `/trace-summary` для subagent verdict:
  `delegation_requested`, `delegation_expected`, `agent_tool_used`,
  `child_runs_started`, `child_runs_completed`, `groups_joined`,
  `parent_synthesized_final`, `child_error_count`.
- [x] Добавить failure labels:
  `missed_explicit_delegation`, `unnecessary_delegation`,
  `subagent_no_final`, `child_result_not_used`, `child_prompt_not_bounded`.
- [x] В live probe считать сценарий успешным только если:
  - tool surface содержит `agent_tool` вместе с выбранными web capabilities;
  - есть `agent_tool` при explicit/autonomous delegation;
  - есть completed child/group join;
  - финальный parent answer содержит синтез child output;
  - нет повторного plan loop после join.

### Chat UI/UX

- [x] Спроектировать `SubagentPanel` вместо сырого tool JSON:
  - компактная карточка "Delegated work";
  - статусы: preparing, spawned, running, joined, failed, cancelled;
  - список child agents с role/title, short task, elapsed time, terminal state;
  - итоговый child summary в 1-3 строки;
  - кнопка/accordion "Inspect" для деталей.
- [x] Детальный просмотр должен показывать полезную работу, а не шум:
  - child prompt / task brief;
  - child final output;
  - used tools summary;
  - warnings/errors;
  - ссылки на child run id и Phoenix trace when available.
- [x] Скрыть raw `agent_tool` JSON по умолчанию:
  - raw payload оставить только в expandable debug view;
  - на основной поверхности показывать человекочитаемый lifecycle.
- [x] Продумать визуальную модель:
  - в общем чате субагенты не должны выглядеть как отдельные полноценные
    собеседники;
  - лучше формат "work packet / worker lane" внутри assistant bubble;
  - parent final answer остается главным артефактом.
- [x] Проверить accessibility:
  - статусы читаются screen reader;
  - keyboard раскрывает child details;
  - long child output не ломает scroll и composer.

### Implementation Slices

- [x] Slice 1: expose Agents preset in demo UI and docs, keep filesystem/shell
  unavailable from web surface.
- [x] Slice 2: add subagent lifecycle projection in frontend event parser:
  group started, child spawned, child completed, group joined, merge completed.
- [x] Slice 3: build deterministic `subagent-final` UI regression around
  `SubagentPanel`.
- [x] Slice 4: add live explicit/autonomous/no-delegation scenarios and Phoenix
  verdict checks.
- [x] Slice 5: prompt/runtime policy tuning from traces, keeping Python Zen
  rule: prefer model + prompt + small guard before graph machinery.
- [x] Slice 6: code quality pass with focused frontend tests, backend tests,
  Playwright screenshots, `black`, `isort`, and meaningful `pylint` fixes for
  touched Python.

## Neighbor Project Findings

### OpenClaude

- Plan mode это permission mode, не просто tool.
- Approval plans нужны для implementation, а не для research/codebase
  understanding.
- Mode instructions инжектятся как attachments, включая sparse reminders.
- `AskUserQuestion` ограничен: 1-4 questions, короткие headers, choices,
  optional "Other"; это не approval mechanism.
- `TodoWrite` это progress telemetry, требует один active step.
- Coordinator/worker prompts запрещают fake worker results и требуют
  self-contained child prompts.

### Hermes Agent

- Base persona маленькая; поведение живёт в focused runtime blocks.
- `kanban_specify` полезен как компактный `Goal / Approach / Acceptance /
  Out of scope` contract.
- `kanban_decompose` и `kanban_swarm` добавляют graph только когда реально
  нужна параллельность.
- Busy input явно делится на `interrupt`, `queue`, `steer`.
- Hooks/plugins дают тонкие extension points без встраивания всех правил в
  core loop.
- Compression protective: сохранить head/recent tail/tool-call integrity и не
  переписывать context бесконечно.

## Next Work: Python Code Executor Autonomy And UX

Цель: сделать Python execution в chat demo такой же естественной способностью
агента, как planning/subagents: инструмент доступен всегда, пользователь его не
включает и не выключает, а модель сама решает, когда расчет/код надежнее
свободного текста.

### Current State

- В `agent_driver` уже есть reusable `python` tool:
  `agent_driver.tools.builtin.python`, `PythonToolSettings`,
  `python_tool_system_addendum.txt`, local subprocess backend, import allowlist,
  session persistence, policy-error remediation и unit tests.
- В chat demo этот tool пока не включен в обычный tool surface: presets
  управляют web search/fetch, а `python_exec` не добавляется как always-on
  capability.
- UI уже умеет скрывать text-form `<|python_tag|>`, но нет красивой карточки
  исполнения: код/result/stderr показываются как обычный tool payload или могут
  утонуть в raw JSON.
- Phoenix `/trace-summary` пока не дает verdict уровня
  `python_expected/python_used/final_matches_python_result`, поэтому live
  сценарий может выглядеть успешным глазами UI, но фактически модель могла
  посчитать сама.

### External And Neighbor Findings

- OpenAI Code Interpreter показывает явную prompt-практику: для math задач
  просить модель писать и запускать код через "python tool"; контейнер
  sandboxed и может быть auto/explicit, а модель видит инструмент как
  `python tool`.
  Source: https://developers.openai.com/api/docs/guides/tools-code-interpreter
- Anthropic tool-use docs разделяют client-executed tools и server-executed
  tools; для client tools приложение должно вести loop
  `tool_use -> execute -> tool_result -> continue`. Для server tools модель
  сама итерирует, но paused turns надо продолжать.
  Source: https://platform.claude.com/docs/en/agents-and-tools/tool-use/how-tool-use-works
- Anthropic code execution docs фиксируют правильную модель продукта:
  Claude сам оценивает, поможет ли code execution, запускает расчеты в
  sandbox и возвращает анализ/результаты; важно явно различать несколько
  execution environments, если они есть.
  Source: https://platform.claude.com/docs/en/agents-and-tools/tool-use/code-execution-tool
- OpenClaude полезен не Python executor как отдельной фичей, а sandbox
  философией: sandbox auto-allow допустим только с явными deny/ask guard,
  prompt должен объяснять ограничения, а failure должен вести к понятному
  recovery, не к повтору опасной команды.
- Hermes Agent держит `execute_code` в core toolset вместе с `delegate_task`;
  schema динамически перечисляет только реально доступные sandbox tools,
  child env чистится от API keys/secrets, stdout/stderr редактируются,
  execution-only calls не съедают iteration budget, а UI показывает короткий
  `exec first_line` preview вместо полного JSON.

### Product Behavior

- [x] Python execution всегда доступен в chat demo runtime surface вместе с
  bounded delegation и planning progress; Web Search/Web Fetch остаются
  пользовательскими переключателями.
- [x] Модель должна использовать `python` для:
  - арифметики, счета символов/слов/строк, процентов, дат и единиц измерения;
  - статистики, вероятностей, комбинаторики, финансовых/табличных расчетов;
  - проверки результата, где LLM склонна ошибаться;
  - преобразования небольших структурированных данных, если это дешевле и
    надежнее ручного рассуждения.
- [x] Модель не должна использовать `python` для приветствий, простых
  переводов, мнений, обычного factual Q&A и задач, где tool latency явно
  дороже ответа.
- [x] После `python` tool call агент обязан дать человекочитаемый финал,
  опирающийся на результат execution, а не останавливаться на tool payload.
- [x] Policy error должен вести к переписыванию кода на allowed imports или к
  краткому объяснению ограничения, без повторения того же blocked import.
  Runtime regression covers blocked `os` import -> recovery through allowed
  `math`, and confirms policy errors do not trigger premature
  `python_result_ready` force-final.

### Runtime / Prompt Slices

- [x] Slice 1: включить `python_exec` в clean chat-demo tool surface для всех
  безопасных presets (`off`, `web_search`, `web_fetch`, `web`) без UI toggle.
- [x] Slice 2: включить `PythonToolSettings(enabled=True, backend="local")` в
  demo `RunnerConfig`; лимиты оставить короткими и понятными для чата,
  `allow_overlay=False`, filesystem/network через Python не открывать.
- [x] Slice 3: уточнить `react_chat_tool_policy.txt`:
  "для расчетов и точного счета сначала используй python, затем синтезируй
  ответ"; "не используй python для тривиального разговора".
- [x] Slice 4: расширить `python_tool_system_addendum.txt` коротким
  decision guide и recovery rule: blocked import is policy, not missing package.
- [x] Slice 5: проверить, не нужен ли маленький runtime guard после успешного
  `python`: следующий LLM step при чистом расчетном запросе получает
  force-final reminder/tool_choice none, чтобы не уходить в лишние tools.
  Implemented `python_reliability_request` runtime reminder and
  `python_result_ready` force-final guard. Also fixed chat initial
  `tool_choice` so it applies only to the first LLM call.

### Chat UI/UX

- [x] Спроектировать `PythonExecutionPanel` вместо raw JSON:
  компактная карточка "Python calculation" / "Code execution";
  статусы queued/running/done/error/timeout; elapsed time; session id только в
  details.
- [x] На основной поверхности показывать:
  короткую цель/первую строку кода, итог stdout/final value, warning/error
  если есть, без горизонтального JSON-scroll.
- [x] В expandable details показывать:
  syntax-highlighted code, stdout, stderr/traceback, limits, allowed imports,
  raw payload для debug.
- [x] Для числовых результатов добавить аккуратные result chips:
  `result`, `rounded`, `exact` если поля можно достать из stdout/JSON без
  сложного парсинга.
- [x] Accessibility: карточка раскрывается клавиатурой, status читается screen
  reader, длинный output не ломает chat scroll/composer.

### Phoenix / Trace Criteria

- [x] Добавить в `/trace-summary` поля:
  `python_tool_available`, `python_tool_used`, `python_calls`,
  `python_policy_errors`, `python_timeouts`, `python_expected`,
  `missed_python_for_calculation`, `python_result_observed`,
  `final_after_python`, `final_mentions_python_error`.
- [x] Добавить scenario verdict labels:
  `missed_python`, `python_no_final`, `python_policy_loop`,
  `unnecessary_python`, `python_result_ignored`.
- [x] Для live scenarios успех означает:
  tool surface содержит `python`;
  расчетные задачи реально вызывают `python`;
  финальный ответ совпадает с stdout/result;
  простые задачи не вызывают `python`;
  UI показывает `PythonExecutionPanel`, а не raw JSON.

### Scenario Set

- [x] `python-count-letters`: "Сколько букв r в strawberry? Проверь точно."
  Ожидаем `python`, финал `3`, без planning/subagent/web.
- [x] `python-arithmetic`: составное выражение с процентами/округлением.
  Ожидаем `python`, финал с кратким расчетом.
- [x] `python-statistics`: среднее/медиана/стандартное отклонение по списку.
  Ожидаем `python`, желательно `statistics` или allowed scientific stack.
- [x] `python-combinatorics`: вероятность/число комбинаций.
  Ожидаем `python`, финал с формулой и результатом.
- [x] `python-no-tool-simple`: приветствие или короткая factual задача.
  Ожидаем отсутствие `python`.
- [x] `web-plus-python`: найти свежие числа через web и посчитать производный
  показатель через `python`. Ожидаем `web_search`/`web_fetch` + `python` +
  финальный синтез.
- [x] `python-policy-recovery`: попытка использовать blocked import в
  deterministic/fake provider path и проверить recovery text/UI error.
  Implemented deterministic fake provider path plus runtime regression:
  first call returns sandbox policy error, second call uses allowed `math`,
  then final synthesis is forced only after successful Python output.

### Implementation Phases

- [x] Phase A: backend enablement and prompt tuning, focused unit tests for
  tool surface and prompt rendering.
- [x] Phase B: frontend `PythonExecutionPanel`, deterministic DOM checks.
- [x] Phase C: trace-summary verdicts and live probe scenario additions.
- [x] Phase D: Phoenix replay on 5-7 scenarios, prompt/runtime tuning by trace
  evidence.
  Latest replay after tuning:
  `python-count-letters` `run_d9c903583571`;
  `python-arithmetic` `run_c0414b14426c`;
  `python-statistics` `run_ff1241653476`;
  `python-combinatorics` `run_8f0adcb9e80e`;
  `web-plus-python` `run_200031367b47`;
  `simple-direct` `run_929fd792e31c`.
  Trace evidence: Python is used for exact calculation/counting, simple-direct
  uses no tools, and `python_result_ready` force-final prevents the previous
  10-call Python loop.
- [x] Phase E: quality pass: `black`, `isort`, focused `pytest`, frontend
  checks, Playwright live probe, meaningful `pylint` fixes for touched Python.
  Current pass fixed new lint issues (line length, unused argument,
  unnecessary comprehension, missing prompt helper docstrings). Remaining
  `pylint` output is structural legacy noise in stage modules/tests, not
  suppressed.

### Planning Approval Resume Fix

- [x] Fixed approved plan metadata shape: resume now stores the approval hash
  inside `force_planning.approved_plan` instead of an unsupported top-level
  `approved_content_hash`, so `PlanningPolicyInput` validates after approval.
- [x] Kept public chat presets free of modal approval tools; deterministic
  plan-approval/resume coverage uses the `dev` preset where `exit_plan_mode_v2`
  is intentionally available.

## Completed Summary

### Core Phases Closed

- Phase 1: plan artifacts, plan approval payloads, approval interrupts,
  chat-demo plan approval rendering, durable plan lifecycle tests.
- Phase 2: force-planning gate, planning hints, policy modes, remediation,
  chat-demo force-planning env/config path.
- Phase 3: steering contracts, command queue, priority/FIFO/cancellation,
  SDK facade.
- Phase 4: steering adapters, chat-demo controls, replay timeline,
  persisted steering controls.
- Phase 4a: optional Instructor spike boundary for structured extraction.
- Phase 5: native `agent_tool` spawn, idempotent child rows, continuation and
  stop controls.
- Phase 6: background subagents, mailbox, notifications, status/collection,
  abort propagation, scheduling budgets.
- Phase 7: coordinator profile, worker definitions, restricted tool surfaces,
  handoff rules, evals.
- Phase 8: child workspace/cwd isolation, bounded artifact refs, cleanup tests.

### Recent Completed Work

- Moved reusable chat-demo logic into `agent_driver`:
  - `agent_driver.context.transcript`;
  - `agent_driver.observability.message_metadata`;
  - `agent_driver.observability.phoenix`;
  - `agent_driver.observability.run_trace_summary`;
  - `agent_driver.adapters` SSE helpers;
  - `agent_driver.runtime.chat_policy`.
- Added Phoenix-backed live probe:
  `examples/chat-demo/frontend/tests/e2e/chat_live_probe.py`.
- Fixed OpenRouter/Qwen text-form tool call loops.
- Added research task contract evidence checks and force-final after research.
- Added `agent_driver.scenario` tracing for live scenario filtering.
- Committed accumulated work as:
  `ad2ecbe Improve chat runtime observability and research flow`.

## Current Verification Snapshot

Latest known good checks:

- focused runtime/observability/adapter/LLM tests passed;
- chat-demo backend tests passed with `asyncio_mode=auto`;
- `black --check` and `isort --check-only` passed on touched files;
- live `research-report` passed with `run_16b2796ea7f0`;
- live `simple-direct` passed with `run_82c3ff4d12fc`;
- after scenario-tag work, live `simple-direct` passed with
  `run_793c1c5a5614`.
- latest 6-scenario live probe passed with run ids:
  `run_939d0ad932ed`, `run_c3a4befd49bc`, `run_1fd22ffa5f16`,
  `run_50dce1de0945`, `run_8bf476954a92`, `run_f62af2122f04`.
- `subagent-synthesis` live passed with `run_0aec5f474a23`:
  `agent_tool` used, one subagent completed, group joined, final answer
  produced without unwanted research.
- `steering-mid-run` live passed with `run_7e50e962c1b8`:
  `enqueue_user_message` was queued, dequeued, applied, and final answer kept
  required web-search evidence.
- Runtime attachment refactor smoke passed:
  `simple-direct` live `run_5ab97707b513`, `research-report` live
  `run_9e79e1b12d0f`.
- Partial compaction now preserves leading system policy verbatim while
  summarizing only the middle prefix; focused compaction tests passed.
- latest 7-scenario live probe passed with run ids:
  `run_b4b5a25caf59`, `run_1305757bddea`, `run_14d441eb6cae`,
  `run_af1b198eb3fa`, `run_b662ac776ff2`, `run_bf797fbf2048`,
  `run_b8ae998bbbe2`.
- latest 9-scenario live probe passed with run ids:
  `run_accde2c30205`, `run_bd40a12ff4ba`, `run_c27db4580225`,
  `run_45d7a37c00d3`, `run_ddb9dc3a6b07`, `run_509710ff14e2`,
  `run_df503c645b13`, `run_e160a397a865`, `run_5b2c65cf69ae`.
- Subagent UX slice passed:
  - `Tools · Agents` was replaced by always-available bounded delegation;
  - Web Search/Web Fetch can coexist with `agent_tool` in the same surface;
  - deterministic `subagent-final` renders `SubagentPanel` instead of raw
    `agent_tool` JSON;
  - frontend parses subagent lifecycle events into visible child rows:
    group started, child started/completed, group joined;
  - live `subagent-synthesis` passed with `run_19fb7d84ec99`;
  - live `subagent-no-delegation-simple` passed with `run_8fd5cb28ff56`:
    no `agent_tool` for a simple prompt even when delegation is available;
  - live `subagent-explicit-delegation` passed with `run_2c1175e14afc`:
    explicit delegation used `agent_tool`, child joined, parent synthesized;
  - live `subagent-autonomous-delegation` passed with `run_23710fbfc304`:
    complex compare/review prompt used `agent_tool`, child joined, parent
    synthesized;
  - Slice 5 tuning added prompt guidance plus a tiny runtime guard:
    after successful subagent group join, the next parent LLM call is forced to
    `tool_choice=none` with `force_final_reason=subagent_group_joined`;
  - retested live subagent suite after the guard:
    `subagent-autonomous-delegation` `run_d15d8f4670b6`,
    `subagent-explicit-delegation` `run_69013e22a57d`,
    `subagent-no-delegation-simple` `run_1a6e5e73ebdf`; all stayed
    `verdict=pass`, with `planning_tool_calls=0` for subagent flows;
  - latest live subagent suite after UI/accessibility/code-quality pass:
    `subagent-autonomous-delegation` `run_0be5842a4522`,
    `subagent-explicit-delegation` `run_501813483836`,
    `subagent-no-delegation-simple` `run_3534caaa18aa`;
- after making delegation always-on and combinable with web tools, live
    probes passed:
    `web-search-final` `run_5f112a3ef6dd`,
    `subagent-autonomous-delegation` `run_a4378cc314d2`,
    `subagent-explicit-delegation` `run_412f2fc4bd1e`,
    `subagent-no-delegation-simple` `run_83de9cb4f773`;
  - `/trace-summary` now reports subagent delegation verdict fields and the
    live run stayed `verdict=pass`.
- Dynamic prompt/Python replay passed on the live dev stack:
  `prompt-surface-no-web` `run_d566d7faef91`,
  `prompt-surface-fetch-only` `run_658c229c86d8`,
  `python-arithmetic` `run_c0414b14426c`,
  `web-plus-python` `run_200031367b47`.
- Focused checks after the latest slice:
  runtime prompt/tool-policy suite passed, chat-demo backend
  `test_run_trace_summary.py`/`test_tools.py`/`test_resume.py` passed,
  frontend `ToolCallCard.test.tsx` passed, `black --check`,
  `isort --check-only`, and `git diff --check` passed.

Dev stack:

- frontend: `http://localhost:5174`;
- backend: `http://localhost:8010`;
- Phoenix: `http://localhost:6006`;
- Phoenix project: `agent-driver-chat-demo`.

## Demo Boundary Notes

Оставляем в chat-demo:

- FastAPI route composition, session title/view models, browser schemas;
- HTTP stream lifecycle registry and transcript persistence glue;
- sample workspace and per-session demo sandbox paths;
- cancellation host adapter over `RunnerConfig.cancellation_probe`;
- frontend rendering, controls and deterministic/live scenario scripts.

Выносим в `agent_driver`:

- runtime policy;
- task contracts;
- observability summaries;
- Phoenix/OpenTelemetry helpers;
- transcript/run mapping helpers;
- SSE parsing/capture helpers;
- provider/runtime compatibility guards.
