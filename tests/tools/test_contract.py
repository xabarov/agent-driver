"""Tests for ToolContract → ToolManifest converter."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentProfile
from agent_driver.contracts.enums import ApprovalMode, SideEffectClass, ToolRisk
from agent_driver.tools import (
    ToolRegistry,
    manifest_from_contract,
    register_contract_tool,
    tool,
)


def test_minimal_contract_produces_manifest_with_defaults() -> None:
    """Smallest valid contract should yield a low-risk manifest with sane defaults."""
    manifest = manifest_from_contract({"name": "ping", "description": "Send a ping."})
    assert manifest.name == "ping"
    assert manifest.description == "Send a ping."
    assert manifest.risk is ToolRisk.LOW
    assert manifest.side_effect is SideEffectClass.NONE
    assert manifest.approval_mode is ApprovalMode.NEVER
    assert manifest.timeout_seconds == 30.0
    assert manifest.output_char_budget == 4000
    assert manifest.idempotent is True
    assert manifest.metadata == {}
    assert manifest.supported_profiles == [
        AgentProfile.TOOL_CALLING,
        AgentProfile.REACT_TEXT,
        AgentProfile.CODE_AGENT,
    ]
    # remediation_hints auto-generated when missing.
    assert manifest.remediation_hints
    assert "ping" in manifest.remediation_hints[0]


def test_risk_intrusiveness_aliases_normalize() -> None:
    """Both numeric (low/medium/high) and intrusiveness aliases map correctly."""
    for alias, expected in (
        ("low", ToolRisk.LOW),
        ("medium", ToolRisk.MEDIUM),
        ("high", ToolRisk.HIGH),
        ("passive", ToolRisk.LOW),
        ("active", ToolRisk.MEDIUM),
        ("exploit", ToolRisk.HIGH),
    ):
        manifest = manifest_from_contract(
            {"name": "t", "description": "x", "risk_level": alias}
        )
        assert manifest.risk is expected, alias


def test_approval_accepts_bool_and_aliases() -> None:
    """Boolean and string aliases both resolve to ApprovalMode."""
    cases = [
        (False, ApprovalMode.NEVER),
        (True, ApprovalMode.ON_POLICY_MATCH),
        ("never", ApprovalMode.NEVER),
        ("on_match", ApprovalMode.ON_POLICY_MATCH),
        ("on_policy_match", ApprovalMode.ON_POLICY_MATCH),
        ("always", ApprovalMode.ALWAYS),
        ("step_by_step", ApprovalMode.STEP_BY_STEP),
        ("step-by-step", ApprovalMode.STEP_BY_STEP),
    ]
    for value, expected in cases:
        manifest = manifest_from_contract(
            {"name": "t", "description": "x", "requires_approval": value}
        )
        assert manifest.approval_mode is expected, value


def test_side_effect_aliases_normalize() -> None:
    """Hyphenated and snake_case side-effect aliases both work."""
    for alias, expected in (
        ("none", SideEffectClass.NONE),
        ("read_only", SideEffectClass.READ_ONLY),
        ("read-only", SideEffectClass.READ_ONLY),
        ("reversible_write", SideEffectClass.REVERSIBLE_WRITE),
        ("irreversible_write", SideEffectClass.IRREVERSIBLE_WRITE),
        ("external_action", SideEffectClass.EXTERNAL_ACTION),
        ("external-action", SideEffectClass.EXTERNAL_ACTION),
    ):
        manifest = manifest_from_contract(
            {"name": "t", "description": "x", "side_effect": alias}
        )
        assert manifest.side_effect is expected, alias


def test_non_identifier_name_drops_code_agent_profile() -> None:
    """Tool ids with hyphens / dots cannot serve the code_agent profile."""
    manifest = manifest_from_contract(
        {"name": "enum4linux-ng", "description": "SMB enumeration."}
    )
    assert AgentProfile.CODE_AGENT not in manifest.supported_profiles
    assert AgentProfile.TOOL_CALLING in manifest.supported_profiles
    assert AgentProfile.REACT_TEXT in manifest.supported_profiles


def test_metadata_preserves_host_specific_fields() -> None:
    """Host-specific extras pass through metadata verbatim."""
    contract = {
        "name": "nuclei",
        "description": "Template-based vulnerability scanner.",
        "risk_level": "high",
        "side_effect": "external_action",
        "requires_approval": "on_match",
        "timeout_seconds": 600,
        "output_char_budget": 16000,
        "max_result_size_chars": 50000,
        "metadata": {
            "queue_category": "web",
            "catalog_group": "vuln_scanning",
            "intrusiveness": "active",
            "cost": "high",
            "requires_trigger": True,
            "execution": "legacy_shell",
            "capabilities": ["web", "vuln-scanning"],
            "stage_tags": ["exploit"],
        },
    }
    manifest = manifest_from_contract(contract)
    assert manifest.name == "nuclei"
    assert manifest.risk is ToolRisk.HIGH
    assert manifest.side_effect is SideEffectClass.EXTERNAL_ACTION
    assert manifest.approval_mode is ApprovalMode.ON_POLICY_MATCH
    assert manifest.timeout_seconds == 600
    assert manifest.output_char_budget == 16000
    assert manifest.max_result_size_chars == 50000
    assert manifest.metadata["queue_category"] == "web"
    assert manifest.metadata["requires_trigger"] is True
    assert manifest.metadata["capabilities"] == ["web", "vuln-scanning"]


def test_unknown_top_level_field_rejected() -> None:
    """Unknown top-level keys raise to catch contract drift early."""
    with pytest.raises(ValueError, match="unknown contract fields"):
        manifest_from_contract(
            {
                "name": "t",
                "description": "x",
                "queue_category": "web",  # belongs in metadata, not top-level
            }
        )


def test_missing_name_rejected() -> None:
    """Empty / missing name is rejected."""
    with pytest.raises(ValueError, match="non-empty 'name'"):
        manifest_from_contract({"description": "x"})


def test_invalid_name_rejected() -> None:
    """Name with disallowed characters is rejected."""
    with pytest.raises(ValueError, match=r"\[A-Za-z0-9_.:-\]\+"):
        manifest_from_contract({"name": "bad name!", "description": "x"})


def test_caller_supplied_remediation_hints_preserved() -> None:
    """Caller-supplied hints replace the auto-generated default."""
    manifest = manifest_from_contract(
        {
            "name": "t",
            "description": "x",
            "remediation_hints": ["Custom hint A.", "Custom hint B."],
        }
    )
    assert manifest.remediation_hints == ["Custom hint A.", "Custom hint B."]


def test_explicit_supported_profiles_accept_string_names() -> None:
    """Profiles passed as string names normalize to AgentProfile enum."""
    manifest = manifest_from_contract(
        {
            "name": "t",
            "description": "x",
            "supported_profiles": ["tool_calling", "react_text"],
        }
    )
    assert manifest.supported_profiles == [
        AgentProfile.TOOL_CALLING,
        AgentProfile.REACT_TEXT,
    ]


@pytest.mark.asyncio
async def test_register_contract_tool_wires_handler_into_registry() -> None:
    """End-to-end: contract + async handler register and execute."""
    registry = ToolRegistry()

    async def run_my_tool(args: dict) -> dict:
        return {"summary": f"ran {args['target']}"}

    manifest = register_contract_tool(
        registry,
        {
            "name": "my_tool",
            "description": "Scans a target.",
            "risk_level": "active",
            "side_effect": "external_action",
            "requires_approval": "on_match",
            "args_schema": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Target URL.",
                    },
                },
                "required": ["target"],
                "additionalProperties": False,
            },
            "metadata": {"queue_category": "web"},
        },
        run_my_tool,
    )
    assert manifest.name == "my_tool"
    tool = registry.get("my_tool")
    assert tool is not None
    out = await tool.handler({"target": "https://example.test"})
    assert out["summary"] == "ran https://example.test"


def test_sync_handler_rejected() -> None:
    """Sync handlers must be rejected at registration time."""
    registry = ToolRegistry()

    def sync_handler(args: dict) -> dict:  # not async
        return {"x": 1}

    with pytest.raises(TypeError, match="async function"):
        register_contract_tool(
            registry,
            {"name": "sync_t", "description": "x"},
            sync_handler,  # type: ignore[arg-type]
        )


def test_metadata_must_be_mapping() -> None:
    """Non-mapping metadata is rejected."""
    with pytest.raises(TypeError, match="metadata must be a Mapping"):
        manifest_from_contract({"name": "t", "description": "x", "metadata": ["bad"]})


def test_tool_helper_uses_docstring_signature_defaults_and_catalog_projection() -> None:
    """SDK tool helper should infer useful metadata from plain async functions."""

    async def lookup_city(city: str, limit: int = 3) -> dict:
        """Lookup city facts."""
        return {"city": city, "limit": limit}

    definition = tool(lookup_city)
    registry = ToolRegistry()
    registry.register(definition.manifest, definition.handler)

    assert definition.manifest.description == "Lookup city facts."
    assert definition.manifest.args_schema is not None
    limit_schema = definition.manifest.args_schema["properties"]["limit"]
    assert limit_schema["type"] == "integer"
    assert limit_schema["default"] == 3
    assert definition.manifest.remediation_hints

    catalog = registry.catalog()
    assert catalog[0]["name"] == "lookup_city"
    assert catalog[0]["risk"] == "low"
