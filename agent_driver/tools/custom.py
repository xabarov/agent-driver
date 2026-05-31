"""Custom tool registration helpers for typed Python functions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import inspect
from types import NoneType, UnionType
from typing import Any, Union, get_args, get_origin

from agent_driver.contracts import (
    AgentProfile,
    ApprovalMode,
    SideEffectClass,
    ToolManifest,
    ToolRisk,
)
from agent_driver.tools.registry import ToolHandler, ToolRegistry


@dataclass(frozen=True, slots=True)
class CustomToolDefinition:
    """Bundle of validated manifest and async handler."""

    manifest: ToolManifest
    handler: ToolHandler


def custom_tool(
    *,
    name: str | None = None,
    description: str | None = None,
    risk: ToolRisk = ToolRisk.LOW,
    side_effect: SideEffectClass = SideEffectClass.NONE,
    approval_mode: ApprovalMode = ApprovalMode.NEVER,
    timeout_seconds: float | None = 30.0,
    output_char_budget: int | None = 4000,
    idempotent: bool = True,
    output_type: str | None = "json",
    output_schema: dict[str, Any] | None = None,
    remediation_hints: list[str] | None = None,
    supported_profiles: list[AgentProfile] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Callable[
    [Callable[..., Awaitable[dict[str, Any]]]],
    Callable[..., Awaitable[dict[str, Any]]],
]:
    """Decorator attaching validated custom-tool metadata to async function."""

    def _decorator(
        func: Callable[..., Awaitable[dict[str, Any]]],
    ) -> Callable[..., Awaitable[dict[str, Any]]]:
        definition = tool_from_function(
            func,
            name=name,
            description=description,
            risk=risk,
            side_effect=side_effect,
            approval_mode=approval_mode,
            timeout_seconds=timeout_seconds,
            output_char_budget=output_char_budget,
            idempotent=idempotent,
            output_type=output_type,
            output_schema=output_schema,
            remediation_hints=remediation_hints,
            supported_profiles=supported_profiles,
            metadata=metadata,
        )
        setattr(func, "__agent_driver_custom_tool__", definition)
        return func

    return _decorator


def tool_from_function(
    func: Callable[..., Awaitable[dict[str, Any]]],
    *,
    name: str | None = None,
    description: str | None = None,
    risk: ToolRisk = ToolRisk.LOW,
    side_effect: SideEffectClass = SideEffectClass.NONE,
    approval_mode: ApprovalMode = ApprovalMode.NEVER,
    timeout_seconds: float | None = 30.0,
    output_char_budget: int | None = 4000,
    idempotent: bool = True,
    output_type: str | None = "json",
    output_schema: dict[str, Any] | None = None,
    remediation_hints: list[str] | None = None,
    supported_profiles: list[AgentProfile] | None = None,
    metadata: dict[str, Any] | None = None,
) -> CustomToolDefinition:
    """Build validated custom tool definition from async function signature."""
    if not inspect.iscoroutinefunction(func):
        raise TypeError("custom tools must be async functions")
    signature = inspect.signature(func)
    schema = _args_schema_from_signature(signature)
    _validate_schema_descriptions(schema)
    effective_hints = _default_remediation_hints(
        tool_name=name or func.__name__,
        remediation_hints=remediation_hints,
    )
    manifest = ToolManifest(
        name=name or func.__name__,
        description=(description or inspect.getdoc(func) or func.__name__).strip(),
        risk=risk,
        side_effect=side_effect,
        approval_mode=approval_mode,
        timeout_seconds=timeout_seconds,
        output_char_budget=output_char_budget,
        idempotent=idempotent,
        args_schema=schema,
        output_type=output_type,
        output_schema=output_schema,
        remediation_hints=effective_hints,
        supported_profiles=(
            list(supported_profiles)
            if supported_profiles is not None
            else [
                AgentProfile.TOOL_CALLING,
                AgentProfile.REACT_TEXT,
                AgentProfile.CODE_AGENT,
            ]
        ),
        metadata=dict(metadata or {}),
    )
    return CustomToolDefinition(
        manifest=manifest,
        handler=_wrap_custom_handler(func, signature),
    )


def register_custom_tool(
    registry: ToolRegistry,
    func: Callable[..., Awaitable[dict[str, Any]]],
) -> ToolManifest:
    """Register decorated custom tool into one registry."""
    definition = getattr(func, "__agent_driver_custom_tool__", None)
    if not isinstance(definition, CustomToolDefinition):
        raise ValueError(
            "function is not decorated with @custom_tool or has invalid metadata"
        )
    registry.register(definition.manifest, definition.handler)
    return definition.manifest


def register_custom_function(
    registry: ToolRegistry,
    func: Callable[..., Awaitable[dict[str, Any]]],
    **manifest_overrides: Any,
) -> ToolManifest:
    """Build and register one custom function tool in a single call."""
    definition = tool_from_function(func, **manifest_overrides)
    registry.register(definition.manifest, definition.handler)
    return definition.manifest


def tool(
    func: Callable[..., Awaitable[dict[str, Any]]] | None = None,
    **manifest_overrides: Any,
) -> (
    CustomToolDefinition
    | Callable[[Callable[..., Awaitable[dict[str, Any]]]], CustomToolDefinition]
):
    """Build a custom-tool definition with SDK-friendly defaults.

    Use as ``tool(my_async_fn)`` for an immediate definition, or
    ``@tool(name="...")`` when a decorator is more convenient. Unlike
    ``@custom_tool``, the decorated object is the ``CustomToolDefinition`` so
    SDK callers can register it directly.
    """

    def _build(
        target: Callable[..., Awaitable[dict[str, Any]]],
    ) -> CustomToolDefinition:
        return tool_from_function(target, **manifest_overrides)

    if func is not None:
        return _build(func)
    return _build


def _args_schema_from_signature(signature: inspect.Signature) -> dict[str, Any]:
    properties: dict[str, dict[str, Any]] = {}
    required: list[str] = []
    for parameter in signature.parameters.values():
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            raise TypeError(
                "custom tools require named parameters and do not support "
                "positional-only, *args, or **kwargs"
            )
        schema = _schema_for_annotation(parameter.annotation)
        schema["description"] = f"Argument '{parameter.name}'."
        if parameter.default is not inspect.Parameter.empty:
            schema["default"] = parameter.default
        properties[parameter.name] = schema
        if parameter.default is inspect.Parameter.empty:
            required.append(parameter.name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _schema_for_annotation(annotation: Any) -> dict[str, Any]:
    if annotation is inspect.Parameter.empty:
        return {"type": "string"}
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (UnionType, Union):
        non_none = [item for item in args if item is not NoneType]
        if len(non_none) == 1 and len(args) == 2:
            return _schema_for_annotation(non_none[0])
        return {"anyOf": [_schema_for_annotation(item) for item in non_none or args]}
    if origin is None:
        return _schema_for_plain_type(annotation)
    if origin is list:
        (inner,) = get_args(annotation) or (str,)
        return {"type": "array", "items": _schema_for_annotation(inner)}
    if origin is dict:
        value_type = args[1] if len(args) > 1 else str
        return {
            "type": "object",
            "additionalProperties": _schema_for_annotation(value_type),
        }
    if origin is tuple:
        return {"type": "array"}
    if origin is NoneType:
        return {"type": "null"}
    if origin is Callable:
        return {"type": "string"}
    if origin is Awaitable:
        return {"type": "string"}
    return {"type": "string"}


def _schema_for_plain_type(annotation: Any) -> dict[str, Any]:
    if annotation in (str, "str"):
        return {"type": "string"}
    if annotation in (int, "int"):
        return {"type": "integer"}
    if annotation in (float, "float"):
        return {"type": "number"}
    if annotation in (bool, "bool"):
        return {"type": "boolean"}
    if annotation in (dict, "dict"):
        return {"type": "object"}
    if annotation in (list, "list"):
        return {"type": "array"}
    if annotation in (Any, "Any"):
        return {}
    return {"type": "string"}


def _validate_schema_descriptions(schema: dict[str, Any]) -> None:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise ValueError("generated args schema must include object properties")
    for name, value in properties.items():
        if not isinstance(value, dict):
            raise ValueError(f"generated args schema for '{name}' must be object")
        description = value.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"generated args schema for '{name}' needs description")


def _default_remediation_hints(
    *, tool_name: str, remediation_hints: list[str] | None
) -> list[str]:
    hints = [item.strip() for item in remediation_hints or [] if item.strip()]
    if hints:
        return hints
    return [
        f"If {tool_name} fails, check the argument values and retry once with a "
        "narrower input.",
        f"If {tool_name} returns empty output, explain that no matching result was "
        "available instead of inventing one.",
    ]


def _wrap_custom_handler(
    func: Callable[..., Awaitable[dict[str, Any]]],
    signature: inspect.Signature,
) -> ToolHandler:
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(args, dict):
            raise ValueError("tool arguments must be an object")
        kwargs: dict[str, Any] = {}
        for parameter in signature.parameters.values():
            if parameter.name in args:
                kwargs[parameter.name] = args[parameter.name]
                continue
            if parameter.default is inspect.Parameter.empty:
                raise ValueError(f"missing required argument '{parameter.name}'")
        unknown = sorted(set(args) - set(signature.parameters))
        if unknown:
            raise ValueError(f"unknown arguments: {', '.join(unknown)}")
        result = await func(**kwargs)
        if not isinstance(result, dict):
            raise ValueError("custom tool handler must return an object")
        return result

    return _handler


__all__ = [
    "CustomToolDefinition",
    "custom_tool",
    "register_custom_function",
    "register_custom_tool",
    "tool",
    "tool_from_function",
]
