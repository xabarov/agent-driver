"""Tool registry discovery/search built-in."""

from __future__ import annotations

from typing import Any

from agent_driver.contracts import ApprovalMode, SideEffectClass, ToolManifest, ToolRisk
from agent_driver.tools.builtin.filesystem._paths import as_int
from agent_driver.tools.registry import ToolRegistry

_TOOL_SEARCH_TOOL = "tool_search"
_DEFAULT_MAX_RESULTS = 100


def register_tool_search_tools(registry: ToolRegistry) -> None:
    """Register tool registry discovery/search tool."""
    registry.register(_tool_search_manifest(), _build_tool_search_handler(registry))


def _tool_search_manifest() -> ToolManifest:
    return ToolManifest(
        name=_TOOL_SEARCH_TOOL,
        description=(
            "Search registered tool manifests by name/description and optional "
            "risk/side-effect filters."
        ),
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.READ_ONLY,
        approval_mode=ApprovalMode.NEVER,
        timeout_seconds=10.0,
        output_char_budget=9000,
        idempotent=True,
        args_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional case-insensitive search query",
                },
                "risk": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": "Optional risk filter",
                },
                "side_effect": {
                    "type": "string",
                    "enum": [
                        "none",
                        "read_only",
                        "reversible_write",
                        "irreversible_write",
                        "external_action",
                    ],
                    "description": "Optional side-effect filter",
                },
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                    "description": "Maximum returned manifests",
                },
                "include_schemas": {
                    "type": "boolean",
                    "description": "Include args/output schemas in response rows",
                },
            },
            "additionalProperties": False,
        },
        output_type="json",
    )


def _build_tool_search_handler(registry: ToolRegistry):
    async def _tool_search_handler(args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query") or "").strip().lower()
        risk_filter = _optional_str(args.get("risk"))
        side_effect_filter = _optional_str(args.get("side_effect"))
        max_results = as_int(
            args.get("max_results"),
            default=_DEFAULT_MAX_RESULTS,
            minimum=1,
        )
        include_schemas = bool(args.get("include_schemas", False))
        rows: list[dict[str, Any]] = []
        for row in registry.list_registered():
            manifest = row.manifest
            if manifest.name == _TOOL_SEARCH_TOOL:
                continue
            if risk_filter and manifest.risk.value != risk_filter:
                continue
            if side_effect_filter and manifest.side_effect.value != side_effect_filter:
                continue
            if query and not _matches_query(manifest, query=query):
                continue
            payload: dict[str, Any] = {
                "name": manifest.name,
                "description": manifest.description,
                "risk": manifest.risk.value,
                "side_effect": manifest.side_effect.value,
                "approval_mode": manifest.approval_mode.value,
                "idempotent": manifest.idempotent,
            }
            if include_schemas:
                payload["args_schema"] = manifest.args_schema
                payload["output_type"] = manifest.output_type
                payload["output_schema"] = manifest.output_schema
            rows.append(payload)
            if len(rows) >= max_results:
                break
        return {
            "summary": f"{len(rows)} tools matched",
            "query": query,
            "risk_filter": risk_filter,
            "side_effect_filter": side_effect_filter,
            "tools": rows,
        }

    return _tool_search_handler


def _matches_query(manifest: ToolManifest, *, query: str) -> bool:
    searchable = f"{manifest.name} {manifest.description}".lower()
    return query in searchable


def _optional_str(raw: Any) -> str | None:
    value = str(raw or "").strip()
    if not value:
        return None
    return value


__all__ = ["register_tool_search_tools"]
