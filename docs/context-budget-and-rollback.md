# Run context budgets and rolling rollback

`AgentRunInput.context_budget` is the supported run-scoped seam for hosts that
know a model call's input and output windows. It controls provider-facing trim,
message and observation caps, recent retention, pressure thresholds, and the
maximum packet sent to full compaction.

```python
from agent_driver.embedding import AgentRunInput, RunContextBudget

run_input = AgentRunInput(
    input="Write the report",
    agent_id="reporter",
    graph_preset="single_react",
    context_budget=RunContextBudget(
        input_tokens=180_000,
        output_tokens=30_000,
        # Optional semantic overrides; omitted values scale from runner defaults.
        max_messages=360,
        max_observations=360,
        protect_recent_messages=60,
        preserve_recent_observations=90,
        max_observation_preview_chars=2700,
        max_compaction_chars=60_000,
    ),
)
```

For the `180000/30000` example with the default 12k baseline, the resolver uses
`max_chars=720000`, `context_window_estimate=210000`, and a 60k compaction
packet. `run_input.max_tokens` remains authoritative when explicitly set;
otherwise a positive typed `output_tokens` becomes the provider ceiling.

For one deprecation window, the runtime also reads
`app_metadata={"context_budget": {"input_tokens": ..., "output_tokens": ...}}`.
The typed field wins when both are present. Invalid legacy values fall back to
runner defaults and are recorded only as a rejection flag; raw values are not
copied into the audit.

## Rolling rollback to 0.2.0rc5

New releases read rc5 state directly because additive fields have defaults. To
write state that a strict rc5 process can read during a rolling rollback, use
the explicit compatibility profile:

```python
import json

from agent_driver.embedding import (
    ROLLBACK_TARGET_0_2_RC5,
    serialize_runtime_state_for_compatibility,
)

result = serialize_runtime_state_for_compatibility(
    runtime_state,
    target=ROLLBACK_TARGET_0_2_RC5,
)
wire_json = json.dumps(result.payload, sort_keys=True, separators=(",", ":"))
```

The writer removes the additive checkpoint revision and approval concurrency
fields and down-converts a typed budget to the legacy app-metadata shape. It
does not mutate the input state. `removed_paths` and `transformed_paths` are a
raw-free audit: they contain field paths only, never messages, evidence, tool
results, secrets, or model reasoning.

The compatibility profile is deliberately explicit. Do not write its payload
as the canonical new-version state after the rollback window closes, because
the removed revision/idempotency fields carry stronger concurrency semantics.
