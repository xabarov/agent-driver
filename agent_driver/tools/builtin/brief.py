"""Runtime brief/message built-in tool."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agent_driver.contracts import (
    ApprovalMode,
    ArtifactKind,
    ContextArtifactRef,
    SensitivityLevel,
    SideEffectClass,
    ToolManifest,
    ToolRisk,
)
from agent_driver.tools.builtin.filesystem._paths import as_int
from agent_driver.tools.registry import ToolRegistry

_BRIEF_TOOL = "brief_tool"
_DEFAULT_MAX_MESSAGE_CHARS = 4_000


def register_brief_tools(registry: ToolRegistry) -> None:
    """Register lightweight runtime brief/message tool."""
    registry.register(_brief_tool_manifest(), _brief_tool_handler)


def _brief_tool_manifest() -> ToolManifest:
    return ToolManifest(
        name=_BRIEF_TOOL,
        description=(
            "Create a runtime brief payload containing message text plus optional "
            "artifact attachments."
        ),
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.NONE,
        approval_mode=ApprovalMode.NEVER,
        timeout_seconds=10.0,
        output_char_budget=9000,
        idempotent=True,
        args_schema={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Brief message text"},
                "channel": {
                    "type": "string",
                    "enum": ["info", "status", "warning"],
                    "description": "Message channel/type",
                },
                "max_message_chars": {
                    "type": "integer",
                    "minimum": 32,
                    "maximum": 20000,
                    "description": "Maximum message chars returned",
                },
                "attachments": {
                    "type": "array",
                    "description": "Optional artifact attachment references",
                },
            },
            "required": ["message"],
            "additionalProperties": False,
        },
        output_type="json",
        metadata={
            "implementation_status": "session_local_state",
            "adapter_kind": "runtime_brief",
            "application_tags": ["discovery", "collaboration"],
        },
    )


async def _brief_tool_handler(args: dict[str, Any]) -> dict[str, Any]:
    message = str(args.get("message") or "").strip()
    if not message:
        raise ValueError("message is required")
    channel = str(args.get("channel") or "info").strip().lower()
    if channel not in {"info", "status", "warning"}:
        raise ValueError("channel must be one of: info, status, warning")
    max_message_chars = as_int(
        args.get("max_message_chars"),
        default=_DEFAULT_MAX_MESSAGE_CHARS,
        minimum=32,
    )
    rendered_message = message[:max_message_chars]
    truncated = len(message) > max_message_chars
    attachments = _normalize_attachments(args.get("attachments"))
    return {
        "summary": f"brief prepared with {len(attachments)} attachments",
        "brief": {
            "channel": channel,
            "message": rendered_message,
            "truncated": truncated,
            "created_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
            "attachments": attachments,
            "format": "runtime_message_attachment",
        },
    }


def _normalize_attachments(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("attachments must be an array")
    rows: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            ref = ContextArtifactRef(
                artifact_id=item.strip(),
                kind=ArtifactKind.OTHER,
                sensitivity=SensitivityLevel.UNKNOWN,
            )
            rows.append({"artifact_ref": ref.model_dump(mode="json"), "label": ""})
            continue
        if not isinstance(item, dict):
            raise ValueError("attachment rows must be strings or objects")
        artifact_id = str(item.get("artifact_id") or "").strip()
        if not artifact_id:
            raise ValueError("attachment.artifact_id is required")
        kind_raw = str(item.get("kind") or ArtifactKind.OTHER.value).strip().lower()
        sensitivity_raw = (
            str(item.get("sensitivity") or SensitivityLevel.UNKNOWN.value)
            .strip()
            .lower()
        )
        ref = ContextArtifactRef(
            artifact_id=artifact_id,
            kind=ArtifactKind(kind_raw),
            uri=_optional_str(item.get("uri")),
            size_bytes=item.get("size_bytes"),
            sensitivity=SensitivityLevel(sensitivity_raw),
            metadata=(
                item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            ),
        )
        rows.append(
            {
                "artifact_ref": ref.model_dump(mode="json"),
                "label": _optional_str(item.get("label")) or "",
            }
        )
    return rows


def _optional_str(raw: Any) -> str | None:
    value = str(raw or "").strip()
    if not value:
        return None
    return value


__all__ = ["register_brief_tools"]
