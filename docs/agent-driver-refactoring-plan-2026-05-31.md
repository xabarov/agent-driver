# Agent Driver Refactoring Plan

Дата: 2026-05-31.

Цель: снизить сложность `agent_driver` без смены архитектурной парадигмы:
оставить маленький composable runtime с контрактами, событиями, governed tools,
checkpoint/resume и chat-demo как интеграционным gate, но убрать накопившиеся
точки, где новая функциональность уже упирается в "metadata bag", длинные
stage-файлы и повторяющиеся backend/provider paths.

## Problem Statement

Кодовая база выросла в полноценный agent runtime примерно на 47k строк Python.
Основные границы уже хорошие: `contracts`, `runtime`, `tools`, `llm`,
`context`, `observability`, `subagents`, `sdk`. Проблема не в отсутствии
модулей, а в том, что несколько модулей стали "узкими горлышками":

- `runtime/single_agent/tool_stage.py`, `llm_step.py`, `steps.py` держат
  сразу transition logic, metadata mutation, event emission, policy nudges и
  feature-specific hooks.
- `RunContext.metadata` стал скрытым state bus для runner, prompt rendering,
  tool loop, planning, research evidence, streaming, resume и UI projection.
- `tools/executor/governed.py` смешивает parsing, alias normalization, policy,
  guardrails, hooks, concurrency, interrupts и result assembly.
- `llm/providers_impl/openai_compatible.py` делает transport, payload mapping,
  streaming, tool-call parsing, cost/usage normalization и provider-specific
  fallback parsing в одном файле.
- storage backends (`memory`, `sqlite`, `jsonl`, `postgres`) имеют общие
  payload/checkpoint операции, но часть сериализации и ordering logic
  повторяется.
- `observability/run_trace_summary.py` уже стал отдельным диагностическим
  продуктом, но пока живет как большой procedural aggregator.

Нужен refactor plan, который уменьшает blast radius будущих изменений
research, compaction, subagents и UI traces, не превращая runtime в тяжелый DAG.

## Current Findings

### Runtime Loop

- `SingleAgentRunner` уже вынесен в mixins и stage functions; это хорошая
  база. Следующий шаг - не новый framework, а более явные stage input/output
  contracts.
- `RunnerConfig` собран из секций, но публично сохраняет много proxy
  properties для совместимости. Это удобно для старых тестов, но мешает понять,
  какие настройки реально принадлежат trimming, compaction, subagents и code
  agent.
- `_store_tool_stage_outputs`, `_post_process_tool_result`,
  `_refresh_force_final_controls`, continuation/finalize logic и research
  evidence gates используют `context.metadata` как shared mutable dict.
- `runtime/single_agent` уже содержит много файлов с разным уровнем
  абстракции: lifecycle stages, config sections, output builders, protocol
  validation, streaming, todo reminders, context-window recovery. Плоская
  директория перестала показывать владельцев кода.

### Tools

- `GovernedToolExecutor` уже имеет правильный порядок: planned calls -> hooks
  -> policy -> guardrails/gate -> partition/concurrency -> envelopes/traces.
  Но эти стадии представлены как методы одного класса, поэтому новые изменения
  по aliasing, concurrency или HITL легко задевают весь executor.
- Built-in tools хорошо покрыты тестами, но registry/manifest/prompt rendering
  не имеют отдельной "tool catalog projection" модели для LLM, UI и docs.

### LLM Providers

- OpenAI-compatible adapter несет слишком много provider-normalization деталей:
  `tool_choice`, response parsing, text-form tool calls, usage/cost metadata,
  streaming event cleanup.
- Anthropic/Ollama/OpenAI-compatible adapters должны иметь общий слой:
  provider-neutral request/response mapping, provider-specific wire mapping,
  transport/retry/logging.

### Context And Compaction

- Token pressure estimation простая и прозрачная, но текущие thresholds
  ориентированы на late compaction. Наблюдение из `docs/add-notes.md` говорит,
  что качество падает заметно раньше: после ~40 процентов контекста агент уже
  может входить в dumb zone.
- Compaction orchestration изолирована, но runtime не дает модели достаточно
  явного, структурированного сигнала: сколько контекста использовано, пора ли
  суммаризировать, делегировать subagent или перейти к synthesis.

### Observability

- Trace summary полезен как quality gate, особенно для research evidence, но
  сейчас это большой файл с несколькими независимыми доменами диагностики.
- Source evidence логика уже выделяется в `observability/source_evidence.py` и
  `runtime/research_evidence.py`; это хороший шаблон для следующих доменных
  summary modules.

### Tests

- Тестовая сетка сильная: runtime, tools, observability, contracts, prompts,
  sdk, code_agent. Это позволяет делать refactor фазами.
- Риск: часть тестов закрепляет внутренние metadata keys вместо публичного
  поведения. Такие тесты полезны, но перед refactor стоит отделить contract
  tests от implementation-shape tests.

## Refactoring Principles

- Делать изменения фазами по 1-3 PR, каждый с behavioral parity tests.
- Не менять публичные contracts без отдельной migration note.
- Не вводить DAG/workflow engine ради красоты. Сохранять deterministic
  step-loop: `run_started -> llm_call -> tool_stage -> finalize`.
- Заменять ad hoc metadata keys typed snapshots постепенно: сначала mirror
  old keys, потом переводить consumers.
- Структура пакетов должна отражать runtime ownership. Если в директории больше
  10-12 файлов и они уже делятся на очевидные домены, создавать подпакеты, а не
  продолжать добавлять соседние файлы с уточняющими названиями.
- При создании подпакетов оставлять совместимые facade imports там, где тесты
  или downstream code могли импортировать старый модуль напрямую.
- Начинать с extraction/refactor, не с feature rewrites.

## Implementation Plan

### Phase 1 - Runtime Metadata Inventory

- [ ] Составить таблицу всех `context.metadata[...]` keys:
  owner, producer, consumer, persistence requirement, UI relevance.
- [ ] Разделить keys на группы:
  `loop_control`, `llm_request`, `tool_stage`, `planning`, `research`,
  `streaming`, `resume`, `observability`, `ui_projection`.
- [ ] Добавить dev-only trace diagnostic для неизвестных или orphan metadata
  keys в конце run.
- [ ] Зафиксировать snapshot в `docs/runtime.md` или отдельной краткой
  странице `docs/runtime-metadata.md`.

### Phase 2 - Typed Runtime Snapshots

- [ ] Ввести маленькие dataclass/pydantic snapshots рядом с владельцами:
  `LoopControlState`, `ToolLoopState`, `PlanningRuntimeState`,
  `ResearchRuntimeState`, `StreamingRuntimeState`.
- [ ] Сделать helpers `get_*_state(context)` и `put_*_state(context, state)`,
  которые пока читают/пишут старые metadata keys.
- [ ] Перевести `llm_step.py`, `tool_stage.py`, `steps.py` на helpers для
  новых изменений; старые keys оставить совместимыми.
- [ ] Добавить contract tests, что serialized `AgentRunOutput.metadata` не
  меняется для существующих сценариев.

### Phase 3 - Package Structure Cleanup

- [ ] Ввести правило package ownership:
  директория должна содержать либо один узкий domain package, либо подпакеты
  с явными владельцами; смешение stage logic, policy, UI projection и helpers
  в одном плоском каталоге считается refactor smell.
- [ ] Перестроить `runtime/single_agent` в подпакеты без behavioral changes:
  - `runtime/single_agent/lifecycle/`:
    `steps.py`, `journal.py`, `resume.py`, `pending.py`;
  - `runtime/single_agent/llm_call/`:
    `llm_step.py`, `llm.py`, `streaming.py`, `protocol_validate.py`;
  - `runtime/single_agent/tool_loop/`:
    `tool_stage.py`, `step_observations.py`, `todo_reminders.py`;
  - `runtime/single_agent/finalization/`:
    `output.py`, `output_builders.py`, `continuation.py`;
  - `runtime/single_agent/context_management/`:
    `compaction_stage.py`, `context_window_recovery.py`;
  - `runtime/single_agent/planning/`:
    `step_planning.py`, `step_events.py`.
- [ ] Оставить старые module paths как thin compatibility shims на один-два
  релизных цикла, например `runtime/single_agent/llm_step.py` импортирует
  `execute_llm_call_step` из нового подпакета.
- [ ] Добавить package-level `__all__` только для реального public/internal
  surface, не экспортируя все helpers.
- [ ] Зафиксировать import policy: соседние подпакеты общаются через фасады
  или typed snapshots, а не через глубокие private imports.
- [ ] Проверить аналогичные кандидаты:
  `tools/executor`, `tools/builtin`, `llm/providers_impl`, `observability`,
  `cli`. Для каждого либо оставить как есть, либо записать целевую структуру.

### Phase 4 - Tool Stage Split

- [ ] Разделить `runtime/single_agent/tool_stage.py` на более узкие modules:
  `tool_stage/apply_outputs.py`, `tool_stage/transitions.py`,
  `tool_stage/planning_events.py`, `tool_stage/research_controls.py`,
  `tool_stage/subagent_outputs.py`.
- [ ] Оставить публичную функцию `execute_tool_stage_step` как фасад.
- [ ] Вынести force-final/continuation policy в чистые функции с input snapshot
  и output decision, чтобы их тестировать без полного runner.
- [ ] Добавить focused tests на transition matrix:
  interrupt, code-agent loop, subagent group, research continuation, final.

### Phase 5 - LLM Step Split

- [ ] Разделить `llm_step.py` на:
  request preparation, compaction integration, provider call, event projection,
  provider error mapping.
- [ ] Вынести HTTP/provider exception mapping в общий helper, чтобы streaming
  и non-streaming failures давали одинаковые terminal metadata.
- [ ] Убрать прямое знание research/planning prompt fragments из LLM step:
  оставить только `PromptAssemblyResult`.
- [ ] Зафиксировать payload-debug behavior тестами, чтобы split не ухудшил
  диагностику provider 400.

### Phase 6 - Governed Tool Executor Pipeline

- [ ] Представить executor как явный pipeline:
  `normalize_calls -> apply_hooks -> evaluate_policy -> apply_gate ->
  partition -> execute -> collect`.
- [ ] Вынести alias normalization (`read_url` -> `web_fetch`) в registry-level
  resolver или отдельный `ToolAliasResolver`.
- [ ] Вынести concurrency limit/env parsing в `tools/executor/concurrency.py`.
- [ ] Вынести interrupt matching / allowed prompts в `tools/executor/hitl.py`.
- [ ] Acceptance: `GovernedToolExecutor.execute` должен стать orchestration
  method, а не местом всей domain logic.

### Phase 7 - Provider Adapter Decomposition

- [ ] Для OpenAI-compatible adapter выделить:
  `wire_payload.py`, `response_parser.py`, `stream_parser.py`,
  `usage_parser.py`, `tool_choice.py`.
- [ ] Ввести provider adapter conformance tests: одинаковые `LlmResponse`
  для tool calls, text-form fallback, usage, streamed deltas, provider errors.
- [ ] Сравнить Anthropic adapter и OpenAI-compatible adapter на общий
  `ProviderResponseNormalizer` без потери provider-specific behavior.
- [ ] Отдельно документировать OpenRouter quirks, потому что это не чистый
  OpenAI API и часто влияет на model routing.

### Phase 8 - Storage Backend Convergence

- [ ] Убедиться, что memory/sqlite/jsonl/postgres используют один источник
  правды для checkpoint payload serialization.
- [ ] Вынести ordering semantics для `latest`/`list_checkpoints` в shared tests:
  created_at ties, parent checkpoint chain, resume after replacement.
- [ ] Добавить backend capability tests как table-driven suite.
- [ ] Уменьшить повторение SQL/JSON payload conversion; storage-specific code
  должен отвечать за persistence, не за runtime-state semantics.

### Phase 9 - Observability Modules

- [ ] Разбить `run_trace_summary.py` на доменные analyzers:
  `research_summary`, `tool_summary`, `provider_summary`,
  `compaction_summary`, `planning_summary`, `streaming_summary`.
- [ ] Оставить CLI/API-compatible фасад `summarize_run_trace(...)`.
- [ ] Каждый analyzer должен принимать typed events/tool traces и отдавать
  маленький dict + warnings/failures.
- [ ] Добавить golden tests на существующие trace examples.

### Phase 10 - Context Pressure And Early Compaction

- [ ] Добавить `context_usage_ratio` в token pressure snapshot.
- [ ] Ввести ранние состояния:
  `ok`, `early_warning`, `delegate_or_summarize`, `compact_recommended`,
  `blocking`.
- [ ] Настроить мягкие nudges около 35-45 процентов контекста:
  суммаризируй, вызови subagent, зафиксируй findings, переходи к synthesis.
- [ ] Оставить 92 процента как emergency compaction/blocking guard, а не
  основной момент принятия решения.
- [ ] Добавить eval scenario на длинную research/code задачу: качество решения
  до и после ранних nudges.

### Phase 11 - CLI And Eval Boundary

- [ ] Разделить `cli/evals.py` на command parsing, scenario loading,
  runner invocation, report rendering.
- [ ] Сделать eval result contract, который можно использовать из CLI,
  pytest и chat-demo backend без копирования.
- [ ] Перенести длинные live сценарии в data fixtures, чтобы изменения eval
  harness не выглядели как behavioral изменения.

### Phase 12 - Public Contract Guardrails

- [ ] Расширить `tests/contracts` snapshot suite для:
  `AgentRunInput`, `AgentRunOutput`, `RuntimeEvent`, `ToolManifest`,
  `ToolTrace`, interrupt/resume payloads.
- [ ] Добавить тест, что новые typed runtime snapshots не протекают в public
  output без явного mapping.
- [ ] Проверять backwards compatibility для `RunnerConfig` legacy kwargs до
  удаления proxy properties.

### Phase 13 - Cleanup And Documentation

- [ ] Обновить `docs/runtime.md`, `docs/builtin-tools.md`,
  `docs/planning-and-control.md` после завершения фаз.
- [ ] Добавить "module ownership map" в `docs/README.md`.
- [ ] Прогнать `pylint` по touched packages и убрать реальные warnings,
  не расширяя global suppressions.
- [ ] В конце каждой фазы приложить короткий Phoenix/chat-demo trace note,
  если фаза меняла chat behavior.

## Suggested Order

1. Phase 1-2: metadata inventory and typed snapshots.
2. Phase 3: package structure cleanup with compatibility shims.
3. Phase 4-5: split runtime stages while behavior is still identical.
4. Phase 6-7: tool executor and provider adapter decomposition.
5. Phase 8-9: storage and observability convergence.
6. Phase 10: context pressure behavior change after the structural cleanup.
7. Phase 11-13: eval/contract/docs hardening.

Такой порядок важен: сначала делаем state visible, потом режем большие файлы,
и только после этого меняем поведение ранней суммаризации/делегирования.

## Acceptance Criteria

- `pytest` default suite проходит без изменения публичного `AgentRunOutput` для
  существующих сценариев.
- `runtime/single_agent/tool_stage.py`, `llm_step.py`,
  `tools/executor/governed.py`, `llm/providers_impl/openai_compatible.py`,
  `observability/run_trace_summary.py` перестают быть местом, куда нужно
  добавлять каждую новую feature-specific ветку.
- `runtime/single_agent` больше не является плоской свалкой stage/helper
  файлов: lifecycle, llm call, tool loop, finalization, planning и context
  management разведены по подпакетам с совместимыми фасадами.
- Новые runtime state helpers имеют владельцев и tests, а прямые записи в
  `context.metadata` для новых features запрещены code-review правилом.
- Trace summary остается CLI/API-compatible, но внутренне состоит из маленьких
  analyzers.
- Early context-pressure nudges появляются до dumb zone и подтверждаются хотя
  бы одним replay/eval сравнением.
- Документация объясняет, где живут contracts, runtime stages, tool pipeline,
  provider normalization, storage и observability.
