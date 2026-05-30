# Agent Driver Docs

This directory is the current documentation entry point for `agent-driver`.
Old exploratory architecture notes were removed; keep new docs short, current,
and tied to code that exists in the repository.

## Start Here

- [Runtime overview](runtime.md) - runner loop, stores, events, tool execution,
  and where durable state lives.
- [Built-in tools](builtin-tools.md) - current tool packs and what each tool is
  for.
- [Planning and control](planning-and-control.md) - live todos, approval plans,
  clarification, steering, and subagents.
- [Chat demo](chat-demo.md) - local demo stack, Phoenix tracing, provider/env
  setup, and concept checks.
- [Testing](testing.md) - focused unit tests, quality pass, live checks, and
  Playwright smoke scenarios.

## Active Work Plans

- [OpenClaude improvement plan](openclaude-improvement-plan-2026-05-29.md) -
  force planning, steerability, subagents, Phoenix findings, and current
  best-of-both backlog.
- [Roadmap](roadmap.md) - short current direction and quality bar.

## Recipes And Patterns

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
