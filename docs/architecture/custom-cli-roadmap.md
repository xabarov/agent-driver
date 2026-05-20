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
  `--base-url`, `--api-key`, `--timeout-s`);
- provider factory supports `fake`, `openrouter`, `vllm`, and `ollama`;
- provider/base/model/key env contract is unified under `AGENT_DRIVER_*`;
- optional `--provider-healthcheck` emits concise status before execution;
- `fake` remains default to keep offline deterministic tests stable.

## 8) Chat tools UX milestone (next stage)

Chat now targets a useful model-visible tool surface by default:

- shared tool flags for `run` and `chat` (`--tools`, `--tool`, `--tool-pack`,
  `--max-tool-risk`, `--allow-dangerous-tools`);
- safe default packs include read/search/web/planning and avoid dangerous
  shell/write tools unless explicitly enabled;
- `/tools` and `/tools verbose` in chat show selected tool surface;
- chat renderer suppresses low-value runtime internals (`node_completed`) and
  keeps operator-relevant events compact.

## 9) Provider tool-calling bridge milestone

OpenAI-compatible provider now participates in tool-calling runtime flow:

- request payload includes OpenAI-compatible function schemas derived from
  selected `ToolManifest` entries;
- completion and stream normalization parse provider `tool_calls` into runtime
  `planned_tool_calls` metadata;
- tool stage can route back to LLM for follow-up answer when finish reason is
  `tool_calls`, enabling practical chat workflows with selected tool packs;
- chat output adds compact tool/warning prefixes and per-run summary counters.

## 10) CLI productization milestone

CLI now includes first product-grade operator workflows around chat runtime:

- layered config and profile resolution (`config show`) from flags/env/config files;
- explicit diagnostics command (`doctor`) with safe output and optional live check;
- persistent chat session metadata with list/show and in-chat resume/history;
- CLI and slash-command resume actions (`approve/reject/edit/cancel/clarify`);
- inspect/export commands for run artifacts and event timelines.

## 11.5) Openclaude-style chat TUI milestone

Chat UX now transitions from basic line prefixes to a richer operator terminal
surface:

- `prompt_toolkit` prompt session with slash-command completion, `@path`
  completion, history, and Enter-submit flow (`Esc+Enter`/`Ctrl+J` for newline);
- `patch_stdout(raw=True)` around interactive loop to keep typed input from
  leaking into streamed assistant output;
- rich welcome panel with provider/model/session metadata, cwd, branch, and mode;
- tips line (`!`, `/`, `@`, interrupt hints) after welcome panel;
- status spinner row during active turns with phase labels
  (`Pondering...` / `Calling <tool>...`);
- markdown-capable assistant rendering with `●` marker on its own row and
  compact tool cards (`● Tool(args)` + `⎿ summary`);
- terminal run-completed event rows suppressed; per-run summary shown only for
  non-trivial turns (tools/warnings/failures);
- local `!command` shell shortcut and double-`Ctrl+C` exit confirmation;
- deterministic plain fallback remains available via `--plain` and in non-TTY
  test harnesses.

## 11.6) Openclaude memory + tool clarity milestone

Follow-up hardening for production-like chat ergonomics:

- chat turns now pass accumulated transcript as `AgentRunInput.messages`, so the
  assistant retains prior context across turns;
- new `/reset` command clears chat memory and rotates `thread_id` in-place;
- tool lifecycle payloads include structured `args` for both
  `tool_call_started` and `tool_call_completed`, enabling accurate tool cards;
- denied/error tool cards render explicit inline reason instead of only generic
  terminal failure context;
- rich status spinner is moved to `Console.status(...)` lifecycle to avoid
  trailing whitespace artifacts under `patch_stdout`;
- chat command constructs runner config with compaction toggles enabled
  (`enable_compaction`, `enable_session_memory_compaction`) for long sessions;
- prompt footer now surfaces context pressure/budget hints (`ctx=...`,
  `budget=...`) to make token pressure visible before hard failures.

## 12) CLI live evaluation milestone

CLI now includes opt-in live evaluation harness for trace-driven quality checks:

- `agent-driver eval run` executes fixed 10-scenario suite with bounded runtime
  limits and artifact capture under `.agent-driver/evals/<timestamp>/`;
- `agent-driver eval inspect` supports summary-level and per-scenario timeline
  inspection with deterministic plain output;
- artifact bundle includes manifest, per-scenario traces, summary scorecard,
  markdown report, and triage grouping;
- live mode is guarded by `AGENT_DRIVER_RUN_LIVE_CLI_EVALS=1`, while
  `--offline` supports deterministic local baseline.
