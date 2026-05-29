# Phase 7 Follow-ups From Smolagents

This document tracks concrete Phase 7 follow-up work inspired by smolagents patterns and aligned with the current `agent-driver` architecture.

References:
- https://github.com/huggingface/smolagents/tree/main/src/smolagents
- https://huggingface.co/blog/smolagents

## Scope

Phase 7 first cut is implemented. This follow-up plan focuses on closing functional gaps for:
- code-action parsing from model output;
- iterative CodeAgent loop (`LLM -> action -> observation -> LLM`);
- stronger sandbox/runtime safety;
- better model-facing prompt/tool ergonomics;
- replay/debug visibility.

## PR Series

### PR 1: `phase7.1-code-action-parser-observations`

Goal: deliver fast wins without changing runtime control flow.

Changes:
- Add `agent_driver/code_agent/parse.py`:
  - parse action from `llm_response.metadata["code_action"]`;
  - fallback to fenced Python block from model text;
  - reject empty/ambiguous payloads fail-closed.
- Update `agent_driver/code_agent/profile.py` to use parser.
- Ensure `CodeAgentExecutionResult.tool_results` is populated in executor path.
- Ensure stdout/stderr observations from code execution are included in next-step context assembly.

Tests:
- Add parser tests for metadata/text/fenced-block paths and invalid payloads.
- Add runtime integration test proving observation preview reaches next LLM request.
- Add JSON-serialization test for `tool_results`.

Acceptance:
- Code action executes when model returns fenced code only.
- Invalid/ambiguous code payload is rejected deterministically.
- Observation preview is bounded and present in subsequent prompt context.

---

### PR 2: `phase7.2-code-agent-prompt-surface`

Goal: make CodeAgent prompt and callable tools explicit and deterministic.

Changes:
- Add CodeAgent-specific prompt renderer:
  - authorized imports;
  - callable tool signatures/docs;
  - `final_answer(...)` contract;
  - safety instructions.
- Integrate with prompt-template/version/hash path where possible.
- Use full deterministic callable docs block from `tool_surface`.

Tests:
- Snapshot tests for rendered CodeAgent prompt.
- Stable hash tests for tool docs rendering.
- Regression tests for profile compatibility and prompt placeholders.

Acceptance:
- Prompt render is deterministic and includes imports/tools/final-answer contract.
- CodeAgent docs/signatures are stable across runs with same registry input.

---

### PR 3: `phase7.3-multistep-code-agent-loop`

Goal: implement iterative CodeAgent execution, not single-shot tool stage.

Changes:
- Extend runtime step transitions for `AgentProfile.CODE_AGENT`:
  - if no final answer yet, continue loop to `llm_call`;
  - preserve bounded observations between iterations;
  - stop on existing limits (`max_steps`, execution limits, policy interrupts).
- Add explicit terminal conditions for CodeAgent iterations.

Tests:
- Two-step and three-step loop tests (`action -> observation -> action -> final_answer`).
- Deterministic replay/order tests for action/observation/final-answer events.
- Limit/timeout boundary tests for loop termination.

Acceptance:
- Multi-step CodeAgent runs finish deterministically.
- Replay trajectory captures each step with stable event ordering.

---

### PR 4: `phase7.4-sandbox-hardening-memory-projection`

Goal: strengthen execution isolation and operator visibility.

Changes:
- Add secondary executor adapter (subprocess or remote worker style) behind existing `CodeActionExecutor` interface.
- Enforce hard wall-clock timeout with reliable termination.
- Improve output projection/replay fields (`memory_projection`, prompt/render artifacts where applicable).

Tests:
- Hanging/infinite-loop code is terminated by timeout.
- Sandbox adapter conformance tests match local executor contract.
- Projection/replay tests validate deterministic, human-readable debug output.

Acceptance:
- Runtime remains responsive under hostile/buggy code actions.
- CodeAgent runs are inspectable via replay/projection without raw log digging.

## Quality Gates Per PR

Run for each PR:
- `.venv/bin/isort agent_driver tests`
- `.venv/bin/black agent_driver tests`
- `.venv/bin/pylint agent_driver tests --disable=duplicate-code`
- `.venv/bin/python -m pytest tests/code_agent tests/runtime tests/evals`
- `.venv/bin/python scripts/check_package_layout.py`
- `git diff --check`

## Notes

- Keep `code_agent` opt-in only (do not make default profile).
- Keep fail-closed behavior for unsafe imports/serialization/execution.
- Keep output budgets strict for observations and tool results.
- Python tool surface now reuses CodeAgent sandbox contracts through a backend
  adapter (`local` implemented, `docker/e2b/wasm` reserved for follow-ups).
