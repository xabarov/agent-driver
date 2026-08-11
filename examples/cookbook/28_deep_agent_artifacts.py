"""Deep-agent artifact pattern: fan out → persist findings → thread light refs (offline).

Coordination C5. On a wide fan-out, returning every worker's full findings up the chat
multiplies the parent's context (~15× on Anthropic's multi-agent research). The artifact
pattern is the fix: each worker's findings are written to the shared workspace, and only a
compact reference (path + summary) threads into the next phase — which reads a file when it
needs the detail. This composes the C4 coordinator with the C5 artifact primitives:

  research phase → capture_group_artifacts → artifact_references → synthesize phase

`share_workspace` gives the children a shared `workspace_cwd` (SDK children don't inherit
one by default), so the synthesize phase can read what the research phase wrote.

    python examples/cookbook/28_deep_agent_artifacts.py
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from agent_driver.llm import FakeProvider
from agent_driver.sdk import (
    CoordinatorPhase,
    SubagentSpec,
    ToolSet,
    artifact_references,
    capture_group_artifacts,
    create_agent,
    run_coordinator,
    share_workspace,
)


async def main() -> int:
    parent = create_agent(
        provider=FakeProvider(response_text="detailed findings for this topic"),
        tools=ToolSet.only(),
    )
    workspace = Path(tempfile.mkdtemp(prefix="deep_agent_"))
    topics = ["pricing", "latency", "safety"]

    def research_specs(prior):  # noqa: ANN001, ANN202
        specs = [
            SubagentSpec(agent_type=f"researcher_{t}", prompt=f"Research {t}")
            for t in topics
        ]
        return share_workspace(specs, workspace)  # children share the workspace

    def synthesize_specs(prior):  # noqa: ANN001, ANN202
        # Persist the research phase's findings to the workspace, thread only the refs.
        artifacts = capture_group_artifacts(
            prior["research"].group, workspace_cwd=workspace
        )
        brief = artifact_references(artifacts)
        return share_workspace(
            [SubagentSpec(agent_type="writer", prompt=f"Write a brief.\n\n{brief}")],
            workspace,
        )

    phases = [
        CoordinatorPhase("research", research_specs),
        CoordinatorPhase("synthesize", synthesize_specs),
    ]
    result = await run_coordinator(parent, phases)

    print("workspace:", workspace)
    for path in sorted((workspace / "artifacts").glob("*.md")):
        print("  wrote", path.relative_to(workspace), f"({path.stat().st_size} bytes)")
    # What the synthesize phase actually received in its prompt — compact refs, not text:
    arts = capture_group_artifacts(result.phase("research").group, workspace_cwd=workspace)
    print("--- refs threaded to synthesize ---")
    print(artifact_references(arts))
    print("satisfied:", result.satisfied)
    return 0 if result.satisfied else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
