# agent-driver

[English](README.md) | [Русский](README.ru.md)

`agent-driver` — это доменно-нейтральный Python runtime для agentic chat
приложений с поддержкой durable execution, governance инструментов и
воспроизводимых runtime-контрактов.

Текущая версия пакета: `0.1.0`

## Что нового в текущей итерации

- Разделение runtime storage и фабричные хелперы для `memory` / `sqlite` /
  `postgres`
- Выбор поверхности инструментов через `ToolSet` и встроенные packs
- Governed pipeline выполнения инструментов с политиками и ограничением вывода
- Базовые блоки для compaction контекста и извлечения session memory
- Точки входа для evaluation и replay с детерминированными регрессионными
  проверками
- Примитивы профиля code-agent и контракты ограниченного выполнения

## Ключевые возможности

- **Durable runtime**: абстракции checkpoint + event log с in-memory, SQLite и
  PostgreSQL backend
- **Tool governance**: registry, manifests, risk/side-effect policy, guardrails
  и детерминированная генерация tool docs
- **Встроенные packs инструментов**: filesystem, shell, web, planning, tasking и
  MCP-адаптеры
- **Human-in-the-loop примитивы**: структурированные вопросы и инструменты
  обновления planning/task состояния
- **Observability и evals**: export трасс, replay-представления, сравнение по
  датасетам

## Требования

- Python `>=3.11`

## Установка

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
```

Опциональные extras:

```bash
pip install -e .[dev]
pip install -e .[cli]
pip install -e .[postgres]
```

## Быстрый старт

```python
from agent_driver.llm import FakeProvider
from agent_driver.runtime import (
    RunnerConfig,
    SingleAgentRunner,
    create_runtime_store_bundle,
    preflight_runtime_store,
    runtime_store_config_from_env,
)

cfg = runtime_store_config_from_env()
ready = preflight_runtime_store(cfg)
if not ready.healthy:
    raise RuntimeError(f"runtime store not ready: {ready.reason}")

bundle = create_runtime_store_bundle(cfg)
runner = SingleAgentRunner(
    provider=FakeProvider(),
    checkpoint_store=bundle.checkpoint_store,
    event_log=bundle.event_log,
    config=RunnerConfig(),
)
```

## Разработка

```bash
.venv/bin/isort agent_driver tests
.venv/bin/black agent_driver tests
.venv/bin/pylint agent_driver tests
.venv/bin/python -m pytest tests
```

Опциональные live-проверки:

```bash
AGENT_DRIVER_RUN_LIVE_TESTS=1 .venv/bin/python -m pytest -m live tests
```

## Карта документации

- Главный индекс: `docs/README.md`
- Обзор архитектуры и extraction-план:
  `docs/agent-driver-analysis-2026-05-18.md`
- Обзор встроенных инструментов: `docs/builtin-tools.md`
- Roadmap реализации: `docs/roadmap.md`
- Refactor и quality backlog: `docs/refactor/README.md`

## Статус проекта

Репозиторий активно развивается вокруг runtime/tooling контрактов, описанных в
`docs/roadmap.md`. Публичный API находится на ранней стадии и может меняться
между минорными итерациями.
