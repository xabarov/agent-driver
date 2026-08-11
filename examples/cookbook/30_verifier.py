"""Independent verifier: validate subagent output before trusting it (offline).

Coordination C8. The MAST verification-gap failure mode is trusting a worker's answer
without an independent check. `verify_subagent_group` runs a skeptical verifier over a
fan-out's results — accept/reject + the concrete issues found — so a synthesis/gate step
only trusts what passed. `votes=` runs an adversarial multi-vote quorum. Here the verifier
provider returns a JSON verdict so the example is fully offline; in production point it at a
small, cheap model (like the eval-layer `LlmJudge`).

    python examples/cookbook/30_verifier.py
"""

from __future__ import annotations

import asyncio

from agent_driver.llm import FakeProvider
from agent_driver.sdk import (
    SubagentJoinPolicy,
    SubagentSpec,
    ToolSet,
    create_agent,
    run_subagent_group,
    verify_subagent_group,
)

# A stand-in verifier: always returns this JSON verdict (a real one calls a small model).
_VERDICT = '{"accepted": true, "confidence": 0.86, "issues": [], "rationale": "grounded"}'


async def main() -> int:
    parent = create_agent(
        provider=FakeProvider(response_text="worker findings"),
        tools=ToolSet.only(),
    )
    specs = [
        SubagentSpec(agent_type=f"worker_{i}", prompt=f"Analyze part {i}") for i in range(3)
    ]
    group = await run_subagent_group(
        parent, specs, join_policy=SubagentJoinPolicy.WAIT_ALL
    )

    # Verify every child (2-vote quorum) before the parent trusts any of it.
    verifier = FakeProvider(response_text=_VERDICT)
    verdicts = await verify_subagent_group(
        group, provider=verifier, task="Analyze the parts correctly", votes=2
    )

    trusted = 0
    for spec, verdict in zip(specs, verdicts):
        mark = "✓ trust" if verdict.accepted else "✗ reject"
        trusted += int(verdict.accepted)
        issues = f" issues={list(verdict.issues)}" if verdict.issues else ""
        print(f"  {spec.agent_type}: {mark} (conf {verdict.confidence:.2f}){issues}")
    print(f"trusted {trusted}/{len(verdicts)} children")
    return 0 if trusted == len(verdicts) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
