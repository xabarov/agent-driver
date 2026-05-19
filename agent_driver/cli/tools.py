"""CLI tool-surface selection helpers."""

from __future__ import annotations

from dataclasses import dataclass

from agent_driver.contracts.enums import ToolRisk
from agent_driver.tools import ToolSet

_DEFAULT_PACKS = ("filesystem_read", "web", "planning")
_DANGEROUS_PACKS = ("shell", "filesystem_write")
_DANGEROUS_TOOL_NAMES = set(ToolSet.packs(*_DANGEROUS_PACKS).names or ())


class CliToolConfigError(ValueError):
    """Raised when CLI tool-surface settings are invalid."""


@dataclass(frozen=True, slots=True)
class CliToolConfig:
    """Tool selection options gathered from CLI flags."""

    tools_mode: str = "default"
    tools: tuple[str, ...] = ()
    tool_packs: tuple[str, ...] = ()
    max_tool_risk: str | None = None
    allow_dangerous_tools: bool = False


def _normalize_names(items: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(item.strip() for item in items if item.strip())
    unique: list[str] = []
    for name in normalized:
        if name not in unique:
            unique.append(name)
    return tuple(unique)


def _parse_risk(raw: str | None) -> ToolRisk | None:
    if raw is None:
        return None
    try:
        return ToolRisk(raw)
    except ValueError as exc:
        raise CliToolConfigError(
            f"Unsupported --max-tool-risk '{raw}'. Use low|medium|high."
        ) from exc


def _build_from_mode(mode: str) -> ToolSet:
    if mode == "default":
        return ToolSet.packs(*_DEFAULT_PACKS)
    if mode == "none":
        return ToolSet.only()
    if mode == "all":
        return ToolSet.all()
    raise CliToolConfigError(
        f"Unsupported --tools '{mode}'. Use default|none|all."
    )


def _toolset_from_explicit(config: CliToolConfig) -> ToolSet:
    names: list[str] = []
    for pack in _normalize_names(config.tool_packs):
        try:
            names.extend(ToolSet.packs(pack).names or ())
        except ValueError as exc:
            raise CliToolConfigError(str(exc)) from exc
    names.extend(_normalize_names(config.tools))
    return ToolSet.only(*names)


def _assert_dangerous_gate(config: CliToolConfig, toolset: ToolSet) -> None:
    if config.allow_dangerous_tools:
        return
    if config.tools_mode == "all":
        raise CliToolConfigError(
            "--tools all requires --allow-dangerous-tools."
        )
    selected_packs = set(_normalize_names(config.tool_packs))
    if selected_packs.intersection(_DANGEROUS_PACKS):
        raise CliToolConfigError(
            "dangerous tool packs (shell/filesystem_write) require --allow-dangerous-tools."
        )
    names = set(toolset.names or ())
    dangerous = sorted(names.intersection(_DANGEROUS_TOOL_NAMES))
    if dangerous:
        raise CliToolConfigError(
            "dangerous tools require --allow-dangerous-tools: " + ", ".join(dangerous)
        )


def build_cli_toolset(config: CliToolConfig) -> ToolSet:
    """Build ToolSet for CLI run/chat from normalized configuration."""
    has_explicit = bool(config.tools) or bool(config.tool_packs)
    toolset = _toolset_from_explicit(config) if has_explicit else _build_from_mode(config.tools_mode)
    _assert_dangerous_gate(config, toolset)
    risk = _parse_risk(config.max_tool_risk)
    if risk is not None:
        toolset = toolset.with_max_risk(risk)
    return toolset


__all__ = ["CliToolConfig", "CliToolConfigError", "build_cli_toolset"]
