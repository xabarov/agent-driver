"""External ToolContract → ToolManifest converter.

This module lets host applications register tools from a declarative
"contract" (plain dict / dataclass / pydantic model) instead of writing a
typed async function and decorating it with ``@custom_tool``. It is intended
for cases where the host already maintains its own tool catalogue (with
domain-specific fields like ``intrusiveness`` / ``cost`` / ``queue_category``)
and wants to expose those tools through ``agent-driver`` without rewriting the
catalogue.

Domain-neutral design: the converter accepts a flexible mapping and normalizes
risk/side-effect/approval hints in a way that any external system can adopt.
Domain-specific extras land in ``ToolManifest.metadata`` verbatim, never as
first-class fields. The converter does not depend on any host application
import.

Example
-------

::

    from agent_driver.tools import register_contract_tool, ToolRegistry

    registry = ToolRegistry()

    async def run_my_tool(args: dict) -> dict:
        # caller-provided handler; receives validated args dict
        return {"summary": f"ran {args['target']}"}

    register_contract_tool(
        registry,
        {
            "name": "my_tool",
            "description": "Scans a target.",
            "risk_level": "active",          # alias for ToolRisk.MEDIUM
            "side_effect": "external_action",
            "requires_approval": "on_match",
            "timeout_seconds": 120,
            "args_schema": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "Target URL."},
                },
                "required": ["target"],
                "additionalProperties": False,
            },
            "metadata": {
                "queue_category": "web",
                "intrusiveness": "active",
                "cost": "medium",
                "requires_trigger": False,
            },
        },
        run_my_tool,
    )
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from agent_driver.contracts import (
    AgentProfile,
    ApprovalMode,
    SideEffectClass,
    ToolManifest,
    ToolRisk,
)
from agent_driver.tools.registry import ToolHandler, ToolRegistry

ContractHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


_RISK_ALIASES: dict[str, ToolRisk] = {
    "low": ToolRisk.LOW,
    "medium": ToolRisk.MEDIUM,
    "high": ToolRisk.HIGH,
    # Intrusiveness-style hints used by some external catalogues.
    "passive": ToolRisk.LOW,
    "active": ToolRisk.MEDIUM,
    "exploit": ToolRisk.HIGH,
}

_SIDE_EFFECT_ALIASES: dict[str, SideEffectClass] = {
    "none": SideEffectClass.NONE,
    "read_only": SideEffectClass.READ_ONLY,
    "read-only": SideEffectClass.READ_ONLY,
    "reversible_write": SideEffectClass.REVERSIBLE_WRITE,
    "irreversible_write": SideEffectClass.IRREVERSIBLE_WRITE,
    "external_action": SideEffectClass.EXTERNAL_ACTION,
    "external-action": SideEffectClass.EXTERNAL_ACTION,
}

_APPROVAL_ALIASES: dict[str, ApprovalMode] = {
    "never": ApprovalMode.NEVER,
    "on_policy_match": ApprovalMode.ON_POLICY_MATCH,
    "on_match": ApprovalMode.ON_POLICY_MATCH,
    "always": ApprovalMode.ALWAYS,
    "step_by_step": ApprovalMode.STEP_BY_STEP,
    "step-by-step": ApprovalMode.STEP_BY_STEP,
}

_VALID_NAME = re.compile(r"[A-Za-z0-9_.:-]+")
_PYTHON_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _normalize_risk(value: Any) -> ToolRisk:
    """Map alias-string or ToolRisk to ToolRisk."""
    if isinstance(value, ToolRisk):
        return value
    if value is None:
        return ToolRisk.LOW
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _RISK_ALIASES:
            return _RISK_ALIASES[normalized]
    raise ValueError(
        f"unknown risk hint: {value!r}; expected one of {sorted(_RISK_ALIASES)}"
    )


def _normalize_side_effect(value: Any) -> SideEffectClass:
    """Map alias-string or SideEffectClass to SideEffectClass."""
    if isinstance(value, SideEffectClass):
        return value
    if value is None:
        return SideEffectClass.NONE
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _SIDE_EFFECT_ALIASES:
            return _SIDE_EFFECT_ALIASES[normalized]
    raise ValueError(
        f"unknown side_effect hint: {value!r}; expected one of "
        f"{sorted(_SIDE_EFFECT_ALIASES)}"
    )


def _normalize_approval(value: Any) -> ApprovalMode:
    """Map alias-string or bool or ApprovalMode to ApprovalMode."""
    if isinstance(value, ApprovalMode):
        return value
    if value is None or value is False:
        return ApprovalMode.NEVER
    if value is True:
        return ApprovalMode.ON_POLICY_MATCH
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _APPROVAL_ALIASES:
            return _APPROVAL_ALIASES[normalized]
    raise ValueError(
        f"unknown approval hint: {value!r}; expected bool or one of "
        f"{sorted(_APPROVAL_ALIASES)}"
    )


def _normalize_profiles(value: Any, *, tool_name: str) -> list[AgentProfile]:
    """Derive supported profiles; drop CODE_AGENT for non-identifier names."""
    if value is None:
        candidates = [
            AgentProfile.TOOL_CALLING,
            AgentProfile.REACT_TEXT,
            AgentProfile.CODE_AGENT,
        ]
    else:
        candidates = []
        for item in value:
            if isinstance(item, AgentProfile):
                candidates.append(item)
                continue
            if isinstance(item, str):
                try:
                    candidates.append(AgentProfile(item.strip().lower()))
                    continue
                except ValueError as exc:
                    raise ValueError(f"unknown profile: {item!r}") from exc
            raise ValueError(f"unknown profile entry: {item!r}")
    is_python_identifier = bool(_PYTHON_IDENT.fullmatch(tool_name))
    if not is_python_identifier:
        candidates = [p for p in candidates if p is not AgentProfile.CODE_AGENT]
    seen: list[AgentProfile] = []
    for profile in candidates:
        if profile not in seen:
            seen.append(profile)
    if not seen:
        raise ValueError(
            f"contract for {tool_name!r} resolved to empty supported_profiles"
        )
    return seen


def _default_remediation_hints(
    *, risk: ToolRisk, approval: ApprovalMode, tool_name: str
) -> list[str]:
    """Generate non-empty remediation hints when caller did not supply them."""
    hints: list[str] = []
    if approval is not ApprovalMode.NEVER:
        hints.append(
            f"If {tool_name} stalls in approval, ask the operator to confirm scope "
            "or pick a less intrusive alternative."
        )
    if risk is ToolRisk.HIGH:
        hints.append(
            f"On error from {tool_name}, do not retry blindly: re-evaluate the plan "
            "and verify target scope before another attempt."
        )
    elif risk is ToolRisk.MEDIUM:
        hints.append(
            f"If {tool_name} returns ambiguous output, narrow arguments (target, "
            "scope, flags) and re-run once."
        )
    else:
        hints.append(
            f"If {tool_name} returns empty results, broaden the input or try a "
            "related tool from the same category."
        )
    return hints


def manifest_from_contract(contract: Mapping[str, Any]) -> ToolManifest:
    """Build a ``ToolManifest`` from a flexible external contract mapping.

    Required keys:

    - ``name`` — stable tool identifier (must match ``[A-Za-z0-9_.:-]+``)

    Recognized optional keys (all with sensible defaults):

    - ``description`` — model-facing description (default: ``""``)
    - ``risk`` / ``risk_level`` — ``ToolRisk`` or string alias
    - ``side_effect`` — ``SideEffectClass`` or string alias
    - ``approval_mode`` / ``requires_approval`` — ``ApprovalMode``, bool, or string
    - ``timeout_seconds`` — positive float or ``None``
    - ``output_char_budget`` — positive int or ``None``
    - ``max_result_size_chars`` — positive int or ``None`` (artifact spill threshold)
    - ``success_field`` — optional boolean output field; falsy values mark
      structured self-reported tool failures
    - ``idempotent`` — bool (default ``True``)
    - ``args_schema`` — JSON schema dict or ``None``
    - ``output_type`` / ``output_schema``
    - ``remediation_hints`` — list[str]; auto-generated when empty / missing
    - ``supported_profiles`` — list[``AgentProfile`` | str]; auto-derived when missing
    - ``metadata`` — arbitrary JSON-serializable mapping (host-specific extras)

    Unknown top-level keys raise ``ValueError`` so contract drift is caught
    early. To pass host-specific fields, place them inside ``metadata``.
    """
    if not isinstance(contract, Mapping):
        raise TypeError("contract must be a Mapping")
    contract_dict = dict(contract)

    name = contract_dict.pop("name", None)
    if not isinstance(name, str) or not name.strip():
        raise ValueError("contract must include a non-empty 'name'")
    name = name.strip()
    if not _VALID_NAME.fullmatch(name):
        raise ValueError(f"contract name {name!r} must match [A-Za-z0-9_.:-]+")

    description = str(contract_dict.pop("description", "") or "").strip()
    if not description:
        description = name

    risk_hint = contract_dict.pop("risk_level", contract_dict.pop("risk", None))
    risk = _normalize_risk(risk_hint)

    side_effect_hint = contract_dict.pop("side_effect", None)
    side_effect = _normalize_side_effect(side_effect_hint)

    approval_hint = contract_dict.pop(
        "requires_approval", contract_dict.pop("approval_mode", None)
    )
    approval_mode = _normalize_approval(approval_hint)

    timeout_seconds = contract_dict.pop("timeout_seconds", 30.0)
    output_char_budget = contract_dict.pop("output_char_budget", 4000)
    max_result_size_chars = contract_dict.pop("max_result_size_chars", None)
    success_field = contract_dict.pop("success_field", None)
    idempotent = bool(contract_dict.pop("idempotent", True))
    args_schema = contract_dict.pop("args_schema", None)
    output_type = contract_dict.pop("output_type", None)
    output_schema = contract_dict.pop("output_schema", None)

    remediation_hints_raw = contract_dict.pop("remediation_hints", None) or []
    remediation_hints = [
        str(item) for item in remediation_hints_raw if str(item).strip()
    ]
    if not remediation_hints:
        remediation_hints = _default_remediation_hints(
            risk=risk, approval=approval_mode, tool_name=name
        )

    supported_profiles = _normalize_profiles(
        contract_dict.pop("supported_profiles", None), tool_name=name
    )

    metadata = contract_dict.pop("metadata", None) or {}
    if not isinstance(metadata, Mapping):
        raise TypeError("contract.metadata must be a Mapping when provided")
    metadata = dict(metadata)

    if contract_dict:
        raise ValueError(
            "unknown contract fields (place host-specific extras under 'metadata'): "
            f"{sorted(contract_dict)}"
        )

    return ToolManifest(
        name=name,
        description=description,
        risk=risk,
        side_effect=side_effect,
        approval_mode=approval_mode,
        timeout_seconds=timeout_seconds,
        output_char_budget=output_char_budget,
        max_result_size_chars=max_result_size_chars,
        success_field=success_field,
        idempotent=idempotent,
        args_schema=args_schema,
        output_type=output_type,
        output_schema=output_schema,
        remediation_hints=remediation_hints,
        supported_profiles=supported_profiles,
        metadata=metadata,
    )


def register_contract_tool(
    registry: ToolRegistry,
    contract: Mapping[str, Any],
    handler: ContractHandler,
) -> ToolManifest:
    """Register a contract-defined tool with a caller-supplied async handler.

    The handler receives the validated ``args`` dict and must return a dict.
    Validation against ``args_schema`` is the responsibility of the host
    application (or the governed-executor's guardrail pipeline); this helper
    only ensures the handler is async.
    """
    if not callable(handler):
        raise TypeError("handler must be callable")
    manifest = manifest_from_contract(contract)
    wrapped = _wrap_contract_handler(handler, tool_name=manifest.name)
    registry.register(manifest, wrapped)
    return manifest


def _wrap_contract_handler(handler: ContractHandler, *, tool_name: str) -> ToolHandler:
    import inspect

    if not inspect.iscoroutinefunction(handler):
        raise TypeError(f"contract handler for {tool_name!r} must be an async function")

    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(args, dict):
            raise ValueError("tool arguments must be an object")
        result = await handler(args)
        if not isinstance(result, dict):
            raise ValueError(
                f"contract handler for {tool_name!r} must return an object"
            )
        return result

    return _handler


__all__ = [
    "ContractHandler",
    "manifest_from_contract",
    "register_contract_tool",
]
