# U1 — Supported embedding facade

Дата создания: 2026-08-02. Статус: **PROPOSED**. Родитель:
[[048-pentestlens-embedding-readiness-goal]]. Происхождение: upstream Goal (host-adoption).

Один документированный поддерживаемый namespace/facade для хостов-встраивателей. Embedder НЕ
должен нуждаться в `runtime.single_agent.*`, internal-миксинах, underscore-модулях/классах,
lifecycle-impl-файлах или provider-impl-путях. Public-API-совместимость явная: stability-policy,
deprecation-policy, supported Python-range, optional extras, машиночитаемый/тест-owned
export-снапшот.

## Что уже есть (не переделываем)

- **Чистые facade-и уже покрывают большинство категорий:**
  - `agent_driver.sdk` (`__all__`, `sdk/__init__.py:49-89`): `Agent`, `AgentDefaults`, `SdkConfig`,
    `create_agent`, `query`, `Session`, `RunHandle`, `RunStream`, subagent-типы,
    `resume_command_from_payload`, `interrupt_to_stream_event`, + ре-экспорт `tool`/`ToolRegistry`/
    `ToolSet`/`CustomToolDefinition`/`register_custom_function`.
  - `agent_driver.runtime` (`runtime/__init__.py __all__`): `SingleAgentRunner`, `RunnerConfig`,
    `CapabilitySettings`, `InMemoryCheckpointStore`, `InMemoryEventLog`, `PostgresRuntimeStore`,
    `SqliteRuntimeStore`, `create_runtime_store_bundle`, `ToolGate*`, `RunAbortHandle`,
    `wrap_governed_executor`.
  - `agent_driver.llm`: `LlmProvider`, `FakeProvider`, `OpenAICompatibleProvider`,
    `resolve_provider`, `ProviderRouteProfile`, `resolve_openai_compatible_route_profile`.
  - `agent_driver.tools`: `ToolRegistry`, `GovernedToolExecutor`, `CustomToolDefinition`,
    `custom_tool`, `register_contract_tool`, `manifest_from_contract`, `ContractHandler`.
  - `agent_driver.contracts`: `InterruptRequest`, `ResumeCommand`.
- **`sdk/factory.py:create_agent(...)`** — уже задуманный one-call-конструктор, принимает
  `provider`, `tools`, `config`, `checkpoint_store`, `event_log`, `command_queue_store`,
  `memory_provider`, `lifecycle_hooks`, `tool_gate`.
- **Два guard-теста**: `tests/test_public_api.py` (`_PUBLIC_SURFACE` hasattr-проверка,
  «mirrors docs/embedding.md») и `tests/contracts/test_public_exports.py` (subset-проверки
  `__all__` + `forbidden`-множество для `runtime.__all__`, держащее границу tools-vs-runtime).
- **`docs/embedding.md`** — канонический supported-surface документ; явно объявляет
  `runtime.single_agent.*`, `*.lifecycle.*`, `_underscore` внутренними.

## Незакрытые gaps (этот эпик)

1. **Категории, требующие reach в non-facade подпакеты** (embedder вынужден импортить не-facade):
   - Протоколы store для реализации host-supplied-хранилища: `CheckpointStore`, `RuntimeEventLog`,
     `CheckpointRecord`, `StorageCapabilities` — только `agent_driver.runtime.storage`, НЕ в
     `runtime.__all__`, НЕ в `embedding.md`. `sdk/factory.py:17` сам импортит этот путь.
   - Command/control store: `CommandQueueStore`, `InMemoryCommandQueueStore`,
     `SqliteCommandQueueStore` — только `agent_driver.runtime.control`; **Postgres-impl нет**
     (только in-memory + sqlite).
   - Протокол lifecycle-hook: `RunLifecycleHook`, `BaseRunLifecycleHook` — в
     `runtime/lifecycle_hooks.py`, НЕ ре-экспортированы facade-ом (facade даёт только
     высокоуровневые `HookChainLifecycleHook`/`RubricLifecycleHook`). `sdk/factory.py:14` импортит
     module-path.
   - Stream/run-lifecycle-проекции: `project_runtime_events`, `project_run_timeline`,
     `summarize_run_lifecycle`, `RunLifecycleSnapshot` — только `agent_driver.runtime.stream`.
2. **`RunnerConfig` и host-cancellation-поля реально в internal `runtime/single_agent/types.py`**
   (facade-alias чист, но `cancellation_probe`/`abort_handle` определены во внутреннем модуле).
3. **Docs↔examples-расхождение** (само по себе баг U1): `embedding.md` объявляет
   `single_agent.*`/underscore внутренними, но `examples/cookbook/*` импортят
   `runtime.single_agent.types`, `runtime.single_agent.config_sections`,
   `tools.builtin.filesystem._paths`, `runtime.storage/control/stream/tool_gate`. Пример-код
   нарушает свой же supported-контракт.
4. **Нет точного export-снапшота** — оба guard-теста пиннят `issubset`, не golden-equality; drift
   публичной поверхности не ловится.
5. **Нет runtime `__version__`** (только `pyproject` 0.1.0) — embedder не может проверить версию
   в рантайме (нужно для U7-handoff-пина).
6. **Нет декларации** stability/deprecation-policy, supported Python-range, optional-extras в
   одном месте как контракт.

## Фазы

A. **Facade-довод**: ре-экспортировать недостающие поддерживаемые категории в
   `agent_driver.runtime.__all__` (или явный `agent_driver.embedding`-namespace): store-протоколы,
   `CommandQueueStore`-семейство, `RunLifecycleHook`/`BaseRunLifecycleHook`, stream-проекции.
   Решить: расширять `runtime` или ввести один `agent_driver.embedding`-агрегат (предпочтительно —
   один namespace, чтобы `embedding.md`-таблица = один импорт-корень).
B. **`__version__`**: добавить `agent_driver.__version__`, синхронизированный с `pyproject`
   (single-source; тест на согласованность — общий с U7).
C. **Export-снапшот**: тест-owned golden-множество полного публичного `__all__` (equality, не
   subset) + deprecation-реестр; drift падает громко. Обновить `embedding.md`-таблицу под facade A.
D. **Починка примеров**: переписать `examples/cookbook/*` на supported-импорты; добавить/усилить
   **один e2e-embedded-пример**: fake-provider + host-stores + один governed fake-tool +
   lifecycle-hook + approval + resume + abort, импортящий **только** supported-имена. Тест
   утверждает: пример не импортит ни одного forbidden-internal (grep-guard в тесте).
E. **Политики**: задокументировать stability/deprecation-policy, supported Python-matrix, extras
   в `embedding.md`. Приёмка: свип, `test_public_api`/`test_public_exports` расширены, CHANGELOG,
   ledger, миграция старых internal-импортов → facade.

## Не в скоупе

- Переписывание внутренностей ради публичного layout (non-goal родителя) — только facade-слой +
  ре-экспорт, реальные определения остаются где есть.
- Postgres command-queue-impl — если понадобится, это фаза U3/U4 (durable control-store), не U1.
- Продуктовые примеры PentestLens.
