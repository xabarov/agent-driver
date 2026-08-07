# agent-driver

[English](README.md) | [Русский](README.ru.md)

`agent-driver` — это доменно-нейтральный Python runtime для agentic chat
приложений с поддержкой durable execution, governance инструментов и
воспроизводимых runtime-контрактов.

**Встраиваете в своё приложение?** Начните с поддерживаемой публичной поверхности
API и политики стабильности: [docs/embedding.md](docs/embedding.md). Готовые
рецепты: [examples/cookbook](examples/cookbook/README.md).

Текущая версия пакета: `0.12.1`

**Релиз 0.12.1** совместимо исправляет bounded final-answer gate из `0.12.0`:
принятая synthesis-only коррекция теперь терминальна и не заменяется лишним
черновиком от generic continuation detector.

## Что нового в текущей итерации

- SDK-точки входа: `create_agent`, `query`, `Session`, `RunHandle`,
  `RunStream`
- Self-consistency запуск через `run_self_consistent`: несколько одинаковых
  прогонов и plurality-vote по ответам
- Typed provider errors, request ids, route profiles и deterministic preflight
  summaries для provider/model диагностики
- SDK trace summaries, support bundles, context-pressure diagnostics и
  redaction-safe provider diagnostics
- Выбор поверхности инструментов через `ToolSet`, built-in packs и `tool(...)`
- Deferred-tool priming, soft-budget final-answer grace, governed tool
  execution, context compaction и evals

## Ключевые возможности

- **SDK facade**: one-shot queries, sessions, streaming helpers, resume helpers,
  custom tools, self-consistency sampling, trace summaries и support bundles
- **Durable runtime**: абстракции checkpoint + event log с in-memory, SQLite и
  PostgreSQL backend, bounded step-loop defaults и budget grace
- **Tool governance**: registry, manifests, risk/side-effect policy, guardrails
  `success_field` failure mapping и детерминированная генерация tool docs
- **Встроенные packs инструментов**: filesystem, shell, web, planning, tasking и
  MCP-адаптеры
- **Human-in-the-loop примитивы**: структурированные вопросы и инструменты
  обновления planning/task состояния
- **Observability и evals**: export трасс, replay-представления, сравнение по
  датасетам
- **Provider diagnostics**: OpenAI-compatible route profiles, deterministic
  preflight summaries, request-shape downgrade notes и single-provider retry

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
import asyncio

from agent_driver.llm import FakeProvider
from agent_driver.sdk import ToolSet, create_agent, summarize_output


async def main() -> None:
    agent = create_agent(
        provider=FakeProvider(response_text="Hello from agent-driver."),
        tools=ToolSet.only(),
    )
    output = await agent.query("Say hello", run_id="demo_run")
    print(output.answer)
    print(summarize_output(output).verdict)


asyncio.run(main())
```

Sessions и streaming используют тот же facade:

```python
session = agent.session("user_123")
stream = session.stream("Draft a concise status update")

async for delta in stream.text_deltas():
    print(delta, end="")
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
cp .env.template .env
set -a && . ./.env && set +a
.venv/bin/python -m pytest -m live tests
```

Шаблон `.env.template` документирует live provider, timeout, runtime-store,
Postgres, server auth и Python tool переменные. Для live provider checks нужны
`AGENT_DRIVER_API_KEY`, `AGENT_DRIVER_BASE_URL`, `AGENT_DRIVER_MODEL` и
`AGENT_DRIVER_RUN_LIVE_TESTS=1`; для Postgres checks дополнительно нужны
`AGENT_DRIVER_RUN_POSTGRES_TESTS=1` и `AGENT_DRIVER_POSTGRES_DSN`.

Частые make targets:

```bash
make test
make format-check
make lint
make selftest-fake
```

## Карта документации

- Встраивание (публичная поверхность API + стабильность): `docs/embedding.md`
- Cookbook с offline runnable examples: `examples/cookbook/`
- Расширение agent-driver: `docs/extending.md`
- Главный индекс: `docs/README.md`
- SDK overview: `docs/sdk.md`
- Sessions: `docs/sdk-sessions.md`
- Tools: `docs/sdk-tools.md`
- Streaming: `docs/sdk-streaming.md`
- Errors: `docs/sdk-errors.md`
- Runtime overview: `docs/runtime.md`
- Planning and control: `docs/planning-and-control.md`
- Provider/model debugging и route preflight:
  `docs/provider-model-debugging.md`
- Node contract: `docs/node-contract.md`
- Chat demo: `docs/chat-demo.md`
- Testing: `docs/testing.md`
- Обзор встроенных инструментов: `docs/builtin-tools.md`
- Server surfaces: `docs/server.md`, `docs/mcp-http.md`, `docs/acp.md`,
  `docs/a2a.md`
- Roadmap: `docs/roadmap.md`

## Статус проекта

Репозиторий активно развивается вокруг runtime/tooling контрактов, описанных в
`docs/roadmap.md`. Публичный API находится на ранней стадии и может меняться
между минорными итерациями.
