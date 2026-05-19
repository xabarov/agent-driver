"""Generate deterministic Python-callable tool surface."""

from __future__ import annotations

from dataclasses import dataclass
from inspect import Signature

from agent_driver.contracts.enums import ApprovalMode, SideEffectClass
from agent_driver.tools.registry import RegisteredTool, ToolRegistry


@dataclass(frozen=True, slots=True)
class CallableToolSpec:
    """Deterministic callable tool descriptor for CodeAgent prompting."""

    name: str
    signature: str
    doc: str
    side_effect: SideEffectClass
    approval_mode: ApprovalMode


def _format_signature(tool: RegisteredTool) -> str:
    """Render deterministic callable signature from tool schema."""
    schema = tool.manifest.args_schema or {}
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    required = set(schema.get("required", [])) if isinstance(schema, dict) else set()
    ordered = sorted(properties) if isinstance(properties, dict) else []
    parts: list[str] = []
    for name in ordered:
        marker = "" if name in required else " = None"
        parts.append(f"{name}: object{marker}")
    return f"({', '.join(parts)}) -> dict[str, object]"


def build_callable_tool_surface(registry: ToolRegistry) -> list[CallableToolSpec]:
    """Build canonical callable tool descriptors from registry."""
    specs: list[CallableToolSpec] = []
    for name in registry.list_names():
        registered = registry.get(name)
        if registered is None:
            continue
        specs.append(
            CallableToolSpec(
                name=name,
                signature=_format_signature(registered),
                doc=registered.manifest.description,
                side_effect=registered.manifest.side_effect,
                approval_mode=registered.manifest.approval_mode,
            )
        )
    return specs


def render_callable_tool_docs(specs: list[CallableToolSpec]) -> str:
    """Render deterministic docs block for prompt/context injection."""
    lines: list[str] = []
    for spec in specs:
        lines.append(f"def {spec.name}{spec.signature}")
        lines.append(f'    """{spec.doc}"""')
        lines.append(
            f"    # side_effect={spec.side_effect.value}, approval={spec.approval_mode.value}"
        )
    return "\n".join(lines)


def callable_signature_map(specs: list[CallableToolSpec]) -> dict[str, Signature]:
    """Build python Signature map for runtime adapters."""
    mapping: dict[str, Signature] = {}
    for spec in specs:
        mapping[spec.name] = Signature.from_callable(lambda **kwargs: kwargs)
    return mapping


__all__ = [
    "CallableToolSpec",
    "build_callable_tool_surface",
    "callable_signature_map",
    "render_callable_tool_docs",
]
