"""Deterministic prompt-facing tool documentation rendering."""

from __future__ import annotations

import json
from hashlib import sha256

from agent_driver.contracts.enums import AgentProfile
from agent_driver.contracts.tools import ToolManifest


def render_tool_doc(manifest: ToolManifest, profile: AgentProfile) -> str:
    """Render one deterministic tool doc block for a target profile."""
    if profile not in manifest.supported_profiles:
        raise ValueError(
            f"tool '{manifest.name}' is not supported for profile '{profile.value}'"
        )
    args_schema = (
        json.dumps(manifest.args_schema, sort_keys=True)
        if manifest.args_schema is not None
        else "none"
    )
    output_schema = (
        json.dumps(manifest.output_schema, sort_keys=True)
        if manifest.output_schema is not None
        else "none"
    )
    remediation = (
        "; ".join(manifest.remediation_hints) if manifest.remediation_hints else "none"
    )
    lines = [
        f"name: {manifest.name}",
        f"profile: {profile.value}",
        f"description: {manifest.description}",
        f"risk: {manifest.risk.value}",
        f"side_effect: {manifest.side_effect.value}",
        f"approval_mode: {manifest.approval_mode.value}",
        f"timeout_seconds: {manifest.timeout_seconds}",
        f"output_char_budget: {manifest.output_char_budget}",
        f"idempotent: {manifest.idempotent}",
        f"args_schema: {args_schema}",
        f"output_type: {manifest.output_type or 'none'}",
        f"output_schema: {output_schema}",
        f"remediation_hints: {remediation}",
    ]
    return "\n".join(lines)


def render_tool_docs(manifests: list[ToolManifest], profile: AgentProfile) -> str:
    """Render deterministic docs for one profile over many manifests."""
    blocks: list[str] = []
    for manifest in sorted(manifests, key=lambda item: item.name):
        if profile in manifest.supported_profiles:
            blocks.append(render_tool_doc(manifest, profile))
    return "\n\n---\n\n".join(blocks)


def rendered_tool_docs_hash(
    manifests: list[ToolManifest], profile: AgentProfile
) -> str:
    """Return stable hash for rendered tool docs."""
    body = render_tool_docs(manifests, profile)
    return sha256(body.encode("utf-8")).hexdigest()
