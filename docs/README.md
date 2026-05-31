# Agent Driver Docs

This directory is the current documentation entry point for `agent-driver`.
Old exploratory architecture notes live under `archive/`; keep new docs short,
current, and tied to code that exists in the repository.

## Start Here

- [Runtime overview](runtime.md) - runner loop, stores, events, tool execution,
  and where durable state lives.
- [Built-in tools](builtin-tools.md) - current tool packs and what each tool is
  for.
- [SDK](sdk.md) - product-facing `Agent`, `Session`, streaming, trace summary,
  support bundle, and errors.
- [Planning and control](planning-and-control.md) - live todos, approval plans,
  clarification, steering, and subagents.
- [Chat demo](chat-demo.md) - local demo stack, Phoenix tracing, provider/env
  setup, and concept checks.
- [Provider and model debugging](provider-model-debugging.md) - OpenRouter,
  Phoenix, reasoning/tool-call failures, and live model matrix practice.
- [Testing](testing.md) - focused unit tests, quality pass, live checks, and
  Playwright smoke scenarios.

## Plans And Status

- [Unified work plan](unified-work-plan-2026-05-31.md) - active cross-stream
  order for the remaining work: artifact-first Deep Research, eval harness,
  storage convergence, optional SDK gateway/tool-server, and docs hygiene.
- [Efficient Deep Research workspace architecture](efficient-deep-research-workspace-architecture-2026-05-31.md) -
  active design for research artifacts, scoped file tools, source storage and
  the long-answer rewrite-loop fix.
- [Provider and model debugging](provider-model-debugging.md) - OpenRouter,
  Phoenix, reasoning/tool-call failures, and live model matrix practice.
- [Research quality summary](research-quality-improvement-plan-2026-05-31.md) -
  completed research baseline, evidence decisions, and acceptance run IDs.
- [Runtime metadata inventory](runtime-metadata.md) - current
  `RunContext.metadata` owner map for runtime state refactoring.
- [Roadmap](roadmap.md) - short current direction, verification loop, and
  quality bar.

## Decision Records

- [Deep Research and Skills analysis](deep-research-and-skills-analysis-2026-05-31.md) -
  compact reference for the shared Skills and source-ledger contracts.
- [SDK quality analysis](sdk-quality-deep-analysis-2026-05-31.md) - reference
  record for SDK productization decisions now reflected in SDK docs.
- [Agent Driver refactoring record](agent-driver-refactoring-plan-2026-05-31.md) -
  compact structural decision record for closed refactor phases and remaining
  storage/eval infrastructure work.
- [Archived May 2026 plans](archive/2026-05/README.md) - closed historical
  phase logs.

## Recipes And Patterns

- [SDK sessions](sdk-sessions.md)
- [SDK tools](sdk-tools.md)
- [SDK streaming](sdk-streaming.md)
- [SDK errors](sdk-errors.md)
- [SDK backend recipes](examples/sdk-backend-recipes.md)
- [SDK toolset examples](examples/sdk-toolset-examples.md)
- [Forcing tool calls](patterns/forcing-tool-calls.md)
- [Multi-mode prompts](patterns/multi-mode-prompts.md)
- [Structured output](patterns/structured-output.md)
- [Nano banana banner prompt](examples/nano-banana-banner-prompt.md)

## Documentation Rules

- Prefer one short page per current concept.
- Do not link to deleted historical docs.
- If a behavior is visible in the chat demo, include how to verify it.
- If a design problem is discovered in UI testing, add a short dated note to
  [Chat demo](chat-demo.md) instead of hiding it in a generic backlog.
- At phase boundaries, record the focused tests, Playwright checks, and any
  `pylint` refactoring pass that were actually run.
