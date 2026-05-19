# Custom CLI roadmap (OpenClaude-informed)

This document translates OpenClaude architecture findings into a reachable
`agent-driver` CLI roadmap without importing OpenClaude implementation details.

## 1) OpenClaude audit (what to reuse vs avoid)

OpenClaude is a complete terminal product, not only a stream formatter. The
main reusable ideas are architectural boundaries and workflow coverage:

- thin startup/bootstrap before loading heavier command stack;
- explicit command surface (`run` + replay/inspect/config families);
- layered settings and provider profile UX;
- session lifecycle with resume/interrupt handling;
- headless modes (SDK-like and transport adapters) that share event vocabulary.

What should not be copied directly for `agent-driver`:

- full React/Ink product stack and custom reconciler;
- large slash-command ecosystem in first release;
- product subsystems that are outside current runtime goals (daemon/IDE bridge
  parity and complex plugin marketplace behavior).

## 2) Current CLI foundation in agent-driver

Current code under `agent_driver/adapters` is a rendering/transport foundation:

- `cli.py` provides deterministic plain-text lines and replay/tail/tree helpers;
- `cli_rich.py` provides optional rich formatting with plain fallback;
- `sse.py` shares the same `RunStreamEvent` projection and reconnect semantics.

This is intentionally not a packaged CLI app yet. Before this work there was:

- no `console_scripts` entrypoint in `pyproject.toml`;
- no product command router (`run`, `replay`, `tail`, `tree`);
- no CLI package for command parsing and runtime-store selection.

## 3) Reachable Phase 10 decomposition

To make progress reviewable, the CLI track is split into bounded subprojects:

1. **Phase 10.1 (installed shell)**:
   packaged `agent-driver` CLI with `run/replay/tail/tree`;
2. **Phase 10.2 (live runtime stream)**:
   true incremental stream path and tail follow behavior;
3. **Phase 10.3 (custom rich design system)**:
   own terminal visual identity over runtime stream events;
4. **Phase 10.4 (interactive controls)**:
   approve/reject/edit/cancel/clarify and run inspection commands;
5. **Phase 10.5 (product parity backlog)**:
   provider/config/export/doctor and higher-level operator workflows.

## 4) MVP delivered in this iteration

MVP scope matches Phase 10.1:

- packaged entrypoint: `agent-driver`;
- new product CLI module: `agent_driver/cli/main.py`;
- implemented commands: `run`, `replay`, `tail`, `tree`;
- optional rich output for `run` (`--rich` or auto when available);
- deterministic plain fallback (`--plain`);
- runtime store wiring for `memory|sqlite|postgres` command options.

The MVP uses `FakeProvider` by default so tests and local runs remain offline
and deterministic.

## 5) Next checkpoints after MVP

- make `Agent.stream(...)` incremental for true live streaming;
- add `tail --follow` over durable event logs;
- extract richer terminal components beyond event-per-line rendering;
- expose resume/approval workflows through CLI commands;
- add snapshot-style CLI output tests for event views and rich/plain modes.

## 6) Chat CLI milestone (current stage)

`agent-driver` now includes first interactive chat command:

- `agent-driver chat` starts prompt loop over one chat session;
- each turn streams assistant output from `RunStreamEvent` token deltas;
- local slash commands exist without model call:
  `/help`, `/exit`, `/quit`, `/clear`, `/runs`, `/replay [run_id]`,
  `/tail [run_id] [last_n]`;
- renderer is chat-oriented (assistant token stream + compact event notes),
  avoiding raw event-line transcript as primary UX;
- current scope remains non-fullscreen and fake-provider-first, matching
  Phase 10.3 foundation goals.

## 7) Provider integration milestone (next stage)

CLI now has explicit provider bootstrap path for product workflows:

- shared provider flags for `run` and `chat` (`--provider`, `--model`,
  `--base-url`, `--api-key`, `--api-key-env`, `--timeout-s`);
- provider factory supports `fake`, `openai-compatible`, and `ollama`;
- OpenAI-compatible env aliases supported for OpenRouter-style usage;
- optional `--provider-healthcheck` emits concise status before execution;
- `fake` remains default to keep offline deterministic tests stable.
