"""Session-local messaging built-in tool."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Any
from uuid import uuid4

from agent_driver.contracts import ApprovalMode, SideEffectClass, ToolManifest, ToolRisk
from agent_driver.tools.registry import ToolRegistry

_SEND_MESSAGE_TOOL = "send_message_tool"
_LIST_PEERS_TOOL = "list_peers_tool"
_TEAM_CREATE_TOOL = "team_create_tool"
_TEAM_DELETE_TOOL = "team_delete_tool"
_TEAM_GET_TOOL = "team_get_tool"
_TEAM_LIST_TOOL = "team_list_tool"

_DEFAULT_PEERS: tuple[dict[str, Any], ...] = (
    {
        "peer_id": "agent.teammate",
        "display_name": "Teammate Agent",
        "status": "online",
        "capabilities": ["summary", "review"],
    },
    {
        "peer_id": "agent.researcher",
        "display_name": "Research Agent",
        "status": "busy",
        "capabilities": ["research", "web_search"],
    },
    {
        "peer_id": "agent.writer",
        "display_name": "Writer Agent",
        "status": "offline",
        "capabilities": ["drafting", "briefs"],
    },
)


@dataclass(slots=True)
class _MessageStore:
    by_thread: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    lock: Lock = field(default_factory=Lock)

    def append(self, *, thread_id: str, event: dict[str, Any]) -> dict[str, Any]:
        """Append one event into thread-local in-memory queue."""
        with self.lock:
            self.by_thread.setdefault(thread_id, []).append(event)
        return event


_MESSAGE_STORE = _MessageStore()


@dataclass(slots=True)
class _TeamStore:
    by_team_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    lock: Lock = field(default_factory=Lock)

    def create(self, *, team: dict[str, Any]) -> dict[str, Any]:
        """Create one team record, rejecting duplicates by team_id."""
        team_id = str(team["team_id"])
        with self.lock:
            if team_id in self.by_team_id:
                raise ValueError(f"team already exists: {team_id}")
            self.by_team_id[team_id] = team
        return team

    def delete(self, *, team_id: str) -> dict[str, Any]:
        """Delete one team record by team_id."""
        with self.lock:
            team = self.by_team_id.pop(team_id, None)
        if team is None:
            raise ValueError(f"unknown team_id: {team_id}")
        return team

    def get(self, *, team_id: str) -> dict[str, Any]:
        """Load one team record by team_id."""
        with self.lock:
            team = self.by_team_id.get(team_id)
        if team is None:
            raise ValueError(f"unknown team_id: {team_id}")
        return dict(team)

    def list(self) -> list[dict[str, Any]]:
        """List all team records in deterministic team_id order."""
        with self.lock:
            team_ids = sorted(self.by_team_id)
            return [dict(self.by_team_id[team_id]) for team_id in team_ids]


_TEAM_STORE = _TeamStore()


def register_messaging_tools(registry: ToolRegistry) -> None:
    """Register session-local messaging tools."""
    registry.register(_send_message_manifest(), _send_message_handler)
    registry.register(_list_peers_manifest(), _list_peers_handler)
    registry.register(_team_create_manifest(), _team_create_handler)
    registry.register(_team_delete_manifest(), _team_delete_handler)
    registry.register(_team_get_manifest(), _team_get_handler)
    registry.register(_team_list_manifest(), _team_list_handler)


def _send_message_manifest() -> ToolManifest:
    return ToolManifest(
        name=_SEND_MESSAGE_TOOL,
        description=(
            "Queue a session-local message payload for teammate/subagent collaboration."
        ),
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.REVERSIBLE_WRITE,
        approval_mode=ApprovalMode.ON_POLICY_MATCH,
        timeout_seconds=10.0,
        output_char_budget=9000,
        idempotent=False,
        args_schema={
            "type": "object",
            "properties": {
                "recipient": {"type": "string", "description": "Logical recipient id"},
                "message": {"type": "string", "description": "Message content"},
                "thread_id": {
                    "type": "string",
                    "description": "Optional thread id; defaults to main",
                },
                "channel": {
                    "type": "string",
                    "enum": ["direct", "group", "broadcast"],
                    "description": "Message channel",
                },
                "metadata": {"type": "object", "description": "Optional metadata"},
            },
            "required": ["recipient", "message"],
            "additionalProperties": False,
        },
        output_type="json",
    )


def _list_peers_manifest() -> ToolManifest:
    return ToolManifest(
        name=_LIST_PEERS_TOOL,
        description=(
            "List session-local collaboration peers with availability and capabilities."
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
                "status": {
                    "type": "string",
                    "enum": ["online", "busy", "offline"],
                    "description": "Optional status filter",
                },
                "capability": {
                    "type": "string",
                    "description": "Optional capability filter",
                },
                "include_offline": {
                    "type": "boolean",
                    "description": "Include offline peers in result set",
                },
            },
            "additionalProperties": False,
        },
        output_type="json",
    )


def _team_create_manifest() -> ToolManifest:
    return ToolManifest(
        name=_TEAM_CREATE_TOOL,
        description=(
            "Create a session-local collaboration team record with optional members."
        ),
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.REVERSIBLE_WRITE,
        approval_mode=ApprovalMode.ON_POLICY_MATCH,
        timeout_seconds=10.0,
        output_char_budget=9000,
        idempotent=False,
        args_schema={
            "type": "object",
            "properties": {
                "team_id": {"type": "string", "description": "Unique team identifier"},
                "display_name": {
                    "type": "string",
                    "description": "Optional user-facing team name",
                },
                "members": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional peer ids",
                },
                "purpose": {"type": "string", "description": "Optional team purpose"},
                "metadata": {"type": "object", "description": "Optional metadata"},
            },
            "required": ["team_id"],
            "additionalProperties": False,
        },
        output_type="json",
    )


def _team_delete_manifest() -> ToolManifest:
    return ToolManifest(
        name=_TEAM_DELETE_TOOL,
        description="Delete a session-local collaboration team record.",
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.REVERSIBLE_WRITE,
        approval_mode=ApprovalMode.ON_POLICY_MATCH,
        timeout_seconds=10.0,
        output_char_budget=9000,
        idempotent=False,
        args_schema={
            "type": "object",
            "properties": {
                "team_id": {"type": "string", "description": "Team identifier"},
            },
            "required": ["team_id"],
            "additionalProperties": False,
        },
        output_type="json",
    )


def _team_get_manifest() -> ToolManifest:
    return ToolManifest(
        name=_TEAM_GET_TOOL,
        description="Load one session-local team record by team_id.",
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.READ_ONLY,
        approval_mode=ApprovalMode.NEVER,
        timeout_seconds=10.0,
        output_char_budget=9000,
        idempotent=True,
        args_schema={
            "type": "object",
            "properties": {
                "team_id": {"type": "string", "description": "Team identifier"},
            },
            "required": ["team_id"],
            "additionalProperties": False,
        },
        output_type="json",
    )


def _team_list_manifest() -> ToolManifest:
    return ToolManifest(
        name=_TEAM_LIST_TOOL,
        description="List session-local team records with optional member filter.",
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.READ_ONLY,
        approval_mode=ApprovalMode.NEVER,
        timeout_seconds=10.0,
        output_char_budget=9000,
        idempotent=True,
        args_schema={
            "type": "object",
            "properties": {
                "member": {
                    "type": "string",
                    "description": "Optional member peer id filter",
                },
            },
            "additionalProperties": False,
        },
        output_type="json",
    )


async def _send_message_handler(args: dict[str, Any]) -> dict[str, Any]:
    recipient = str(args.get("recipient") or "").strip()
    if not recipient:
        raise ValueError("recipient is required")
    message = str(args.get("message") or "").strip()
    if not message:
        raise ValueError("message is required")
    thread_id = str(args.get("thread_id") or "main").strip() or "main"
    channel = str(args.get("channel") or "direct").strip().lower()
    if channel not in {"direct", "group", "broadcast"}:
        raise ValueError("channel must be one of: direct, group, broadcast")
    metadata = args.get("metadata")
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")
    event = _MESSAGE_STORE.append(
        thread_id=thread_id,
        event={
            "message_event_id": f"msg_{uuid4().hex[:12]}",
            "recipient": recipient,
            "thread_id": thread_id,
            "channel": channel,
            "message": message,
            "metadata": metadata,
            "created_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
            "delivery": "session_local_queue",
        },
    )
    return {
        "summary": f"message queued for {recipient} on {thread_id}",
        "message_event": event,
    }


async def _list_peers_handler(args: dict[str, Any]) -> dict[str, Any]:
    status = str(args.get("status") or "").strip().lower() or None
    if status is not None and status not in {"online", "busy", "offline"}:
        raise ValueError("status must be one of: online, busy, offline")
    capability = str(args.get("capability") or "").strip().lower() or None
    include_offline = bool(args.get("include_offline", False))
    peers: list[dict[str, Any]] = []
    for row in _DEFAULT_PEERS:
        peer_status = str(row["status"]).lower()
        if not include_offline and peer_status == "offline":
            continue
        if status is not None and peer_status != status:
            continue
        caps = [str(item) for item in row.get("capabilities", [])]
        if capability is not None and capability not in {item.lower() for item in caps}:
            continue
        peers.append(
            {
                "peer_id": row["peer_id"],
                "display_name": row["display_name"],
                "status": row["status"],
                "capabilities": caps,
                "provenance": "session_local_directory",
            }
        )
    return {
        "summary": f"{len(peers)} peers listed",
        "peers": peers,
    }


async def _team_create_handler(args: dict[str, Any]) -> dict[str, Any]:
    team_id = str(args.get("team_id") or "").strip()
    if not team_id:
        raise ValueError("team_id is required")
    display_name = str(args.get("display_name") or team_id).strip() or team_id
    members_raw = args.get("members")
    members: list[str] = []
    if members_raw is not None:
        if not isinstance(members_raw, list):
            raise ValueError("members must be an array of strings")
        for item in members_raw:
            member = str(item).strip()
            if not member:
                continue
            members.append(member)
    purpose = str(args.get("purpose") or "").strip()
    metadata = args.get("metadata")
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")
    created = _TEAM_STORE.create(
        team={
            "team_id": team_id,
            "display_name": display_name,
            "members": members,
            "purpose": purpose,
            "metadata": metadata,
            "created_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
            "provenance": "session_local_team_store",
        }
    )
    return {
        "summary": f"team created: {team_id}",
        "team": created,
    }


async def _team_delete_handler(args: dict[str, Any]) -> dict[str, Any]:
    team_id = str(args.get("team_id") or "").strip()
    if not team_id:
        raise ValueError("team_id is required")
    deleted = _TEAM_STORE.delete(team_id=team_id)
    return {
        "summary": f"team deleted: {team_id}",
        "deleted_team": deleted,
    }


async def _team_get_handler(args: dict[str, Any]) -> dict[str, Any]:
    team_id = str(args.get("team_id") or "").strip()
    if not team_id:
        raise ValueError("team_id is required")
    team = _TEAM_STORE.get(team_id=team_id)
    return {
        "summary": f"team loaded: {team_id}",
        "team": team,
    }


async def _team_list_handler(args: dict[str, Any]) -> dict[str, Any]:
    member = str(args.get("member") or "").strip() or None
    teams = _TEAM_STORE.list()
    if member is not None:
        teams = [row for row in teams if member in row.get("members", [])]
    return {
        "summary": f"{len(teams)} teams listed",
        "teams": teams,
    }


def _reset_message_store_for_tests() -> None:
    with _MESSAGE_STORE.lock:
        _MESSAGE_STORE.by_thread.clear()
    with _TEAM_STORE.lock:
        _TEAM_STORE.by_team_id.clear()


__all__ = ["register_messaging_tools", "_reset_message_store_for_tests"]
