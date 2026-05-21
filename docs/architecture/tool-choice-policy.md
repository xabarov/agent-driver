# Tool-choice policy — scoring and antipattern detection

This note describes the policy-aware tool-choice primitives in
`agent_driver.tools.policy.scoring`. They cover two related needs that
host applications typically implement one-off per product:

1. **Preference scoring.** Bias the model toward tools that fit the
   current context (specialized over generic, read-only over invasive,
   matched-scope over unrelated, ...).
2. **Antipattern detection.** Flag obviously suboptimal sequences such as
   "the model fell back to a generic shell right after a focused
   `tool_search` call returned narrow matches."

The runtime supplies generic primitives; the host wires its own catalog
of rules and decides where to emit metrics or SSE warnings.

## Primitives

| Symbol | Purpose |
| ------ | ------- |
| `ToolChoiceContext` | Inputs: `recent_tool_calls`, `candidate_tools`, `metadata`. |
| `ToolChoiceScore` | Output of scoring: `tool_name`, `score`, `reasons`. |
| `AntipatternMatch` | Output of detection: `pattern_id`, `severity`, `description`, `matched_recent_tool`, `matched_current_tool`, `metadata`. |
| `PreferenceRule` | `Callable[[ToolManifest, ToolChoiceContext], tuple[float, str | None]]` — returns `(delta, reason)`; `delta == 0` skips the reason tag. |
| `AntipatternRule` | `Callable[[str, ToolChoiceContext], AntipatternMatch | None]`. |
| `ToolChoicePolicyRegistry` | Composes rules; provides `score_candidates(...)` and `detect_antipatterns(...)`. |
| `antipattern_to_warning_payload(match)` | Projects an `AntipatternMatch` into a runtime warning payload that the existing `agent_driver.adapters.project_warning_event` projector recognizes (`kind="tool_choice_antipattern"`). |
| `build_default_tool_choice_registry()` | Returns a registry pre-loaded with the reference built-in rules. |

## Reference built-in rules

The runtime ships one rule per direction so callers have a working
example without forking the contract:

- **Preference.** `prefer_specialized_over_generic(manifest, context)`
  returns a small positive delta when
  `manifest.metadata["capabilities"]` is a non-empty iterable. Hosts
  layering richer signals (stage tags, scope alignment, recall hits)
  typically combine this with their own rules.
- **Antipattern.** `generic_after_specialized_search(chosen_tool, context, ...)`
  flags a generic-shell pick (`bash`/`shell`/`execute_command` by default)
  that follows a specialized search call (`tool_search` by default). Both
  tool-name sets are kwargs so hosts can extend them (e.g. ZION may add
  `"recall"` to `specialized_search_tool_names`).

## Safety guarantees

`ToolChoicePolicyRegistry` isolates rule failures so a broken rule does
not crash an export or a run:

- **Scoring.** A rule that raises contributes a `delta` of zero and adds
  a synthetic `rule_error:<id>:<ExcClass>` entry to the candidate's
  `reasons`. A rule that returns a non-numeric delta adds
  `rule_invalid_delta:<id>` and is ignored.
- **Antipattern detection.** A raising rule produces a synthetic
  `AntipatternMatch(pattern_id=f"rule_error:{id}", severity="info")`.
  A rule that returns a non-`AntipatternMatch` value produces
  `rule_invalid_return:{id}`. Hosts can filter these synthetic matches
  out before emission if desired.

## Wiring into runtime warnings

`AntipatternMatch` projects into a stable warning payload via
`antipattern_to_warning_payload(match)`:

```python
{
    "kind": "tool_choice_antipattern",
    "signal_id": match.pattern_id,
    "severity": "info" | "warning" | "critical",
    "description": match.description,
    "matched_recent_tool": ...optional...,
    "matched_current_tool": ...optional...,
    "rule_metadata": ...optional...,
}
```

This payload is the contract consumed by
`agent_driver.adapters.project_warning_event`, so SSE consumers that
already handle `token_pressure` warnings (see
`docs/architecture/warning-events.md`) automatically recognize the new
`tool_choice_antipattern` kind. The application layer maps each
`signal_id` to its own user-facing message and metric label — the runtime
intentionally stays out of the host's metric vocabulary.

## Reference host adapter

```python
from agent_driver.tools.policy import (
    ToolChoiceContext,
    antipattern_to_warning_payload,
    build_default_tool_choice_registry,
)


policy_registry = build_default_tool_choice_registry()
# host-specific extensions
policy_registry.register_antipattern("recall_then_shell", recall_then_shell_rule)


def on_tool_call_completed(tool_name: str, recent_calls: list[str]) -> None:
    context = ToolChoiceContext(
        recent_tool_calls=tuple(recent_calls),
        candidate_tools=(),  # detection does not require candidates
    )
    for match in policy_registry.detect_antipatterns(tool_name, context):
        payload = antipattern_to_warning_payload(match)
        emit_runtime_warning(payload)  # host-specific emission
        host_metrics.tool_choice_antipattern(
            pattern=payload["signal_id"], severity=payload["severity"]
        )
```

The host owns:

- when to call `score_candidates` (typically after `tool_search` or
  during tool-pool assembly);
- when to call `detect_antipatterns` (typically after a tool call
  completes);
- how to translate `signal_id` to product-specific messages, metrics,
  and dashboards.

## Adding new rules

To add a new preference or antipattern rule:

1. Write a plain function that matches the `PreferenceRule` /
   `AntipatternRule` signature.
2. Register it on the host's `ToolChoicePolicyRegistry` with a stable
   `rule_id` (used in error tags and reason strings).
3. If the rule reports a recurring sequence (e.g. a multi-step heuristic),
   give the `AntipatternMatch` a stable `pattern_id` — that is the
   `signal_id` consumers will see and use for metric labels.
4. Add a unit test that exercises both the matching and the no-match
   paths.

There is no need to change the runtime to add new rules — only the
documentation in this file or in the host's own playbook.
