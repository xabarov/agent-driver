"""Shared helpers for local intent/request envelope payloads."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


def build_intent_payload(
    *,
    source_tool: str,
    adapter_kind: str,
    id_prefix: str,
    id_field: str = "event_id",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach standardized observability fields to one intent payload."""
    row = dict(payload or {})
    row[id_field] = f"{id_prefix}_{uuid4().hex[:10]}"
    row["created_at"] = _utc_now()
    row["adapter_kind"] = adapter_kind
    row["provenance"] = {
        "source_tool": source_tool,
        "kind": "intent_envelope",
    }
    return row


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


__all__ = ["build_intent_payload"]
