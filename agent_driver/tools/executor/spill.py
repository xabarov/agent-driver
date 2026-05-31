"""Phase 12 H18 — disk-spill helpers for oversized tool handler outputs.

When a tool returns a payload larger than its
``ToolManifest.max_result_size_chars``, the executor persists the full
payload to an :class:`agent_driver.context.artifacts.ArtifactStore` and
replaces the in-context value with a wrapper carrying a small preview
+ artifact reference. The LLM sees a ``<persisted-output>`` marker in
the observation and can fetch the full payload via ``read_artifact``
if it needs to.

This module exposes:

* :func:`spill_payload_to_artifact` — given a raw handler output and a
  configured store, serialize + persist + return the in-context
  replacement dict + the reference.
* :func:`should_spill_payload` — sentinel decision: ``True`` only when
  the manifest has ``max_result_size_chars`` set AND the encoded
  payload exceeds it AND a store is available.
* :data:`PREVIEW_MAX_CHARS` — default 2 KB preview cap (matches openclaude).

Failure modes:

* Encoding error → returns ``None``; caller falls back to legacy
  truncation. We don't crash the run over an unspillable payload.
* Store ``put`` raises → caller logs WARNING, falls back to legacy
  truncation. The handler's raw output stays in-context (truncated).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from agent_driver.contracts.context import (
    ArtifactPreview,
    ContextArtifactRef,
    StoredArtifact,
)
from agent_driver.contracts.enums import ArtifactKind, SensitivityLevel
from agent_driver.context.artifacts.protocols import ArtifactStore
from agent_driver.tools.context import get_workspace_jail_root

logger = logging.getLogger(__name__)

# Default preview cap; matches openclaude's 2 KB convention so model
# observations stay short even for very large spilled payloads.
PREVIEW_MAX_CHARS = 2000


def _safe_json_dumps(payload: Any) -> str | None:
    """Best-effort JSON encode; returns ``None`` on failure."""
    try:
        return json.dumps(payload, ensure_ascii=True, default=str)
    except Exception:  # pragma: no cover - defensive
        logger.warning("spill: payload not JSON-serializable; falling back to str()")
        try:
            return str(payload)
        except Exception:
            return None


def should_spill_payload(
    *,
    payload: Any,
    max_result_size_chars: int | None,
    store: ArtifactStore | None,
) -> bool:
    """Phase 12 H18 — return True when the executor should spill.

    Decision rules (all must hold):
    * ``store`` is configured (non-None) — host explicitly opted in;
    * ``max_result_size_chars`` is a positive integer (manifest opted in);
    * the encoded payload exceeds the threshold.

    Returns False on any encoding error so the caller falls back to
    the legacy ``_bounded_structured_output`` truncation behaviour.
    """
    if store is None:
        return False
    if not isinstance(max_result_size_chars, int) or max_result_size_chars <= 0:
        return False
    encoded = _safe_json_dumps(payload)
    if encoded is None:
        return False
    return len(encoded) > max_result_size_chars


def _build_preview_text(
    encoded: str, *, limit: int = PREVIEW_MAX_CHARS
) -> ArtifactPreview:
    """Trim ``encoded`` to ``limit`` chars + add ellipsis marker.

    Preview is text-only (no semantic parsing): the model sees the
    first ~2 KB of the JSON dump and a footnote telling it the
    full payload is in an artifact. The model can choose to fetch
    via ``read_artifact`` if it actually needs the rest.
    """
    if len(encoded) <= limit:
        return ArtifactPreview(
            text=encoded,
            truncated=False,
            original_size_bytes=len(encoded),
        )
    return ArtifactPreview(
        text=encoded[:limit] + "…",
        truncated=True,
        original_size_bytes=len(encoded),
    )


def spill_payload_to_artifact(
    *,
    payload: Any,
    store: ArtifactStore,
    tool_name: str,
    run_id: str | None = None,
    tool_call_id: str | None = None,
) -> tuple[dict[str, Any], ContextArtifactRef] | None:
    """Persist ``payload`` to the artifact store, return in-context replacement.

    Returns ``None`` when encoding or storage fails — caller falls
    back to legacy truncation.

    The in-context replacement dict has shape::

        {
          "summary": "<persisted-output> 2 KB preview ...",
          "persisted_artifact": {
              "artifact_id": "...",
              "kind": "tool_result",
              "size_bytes": <full size>,
              "tool_name": "...",
          },
          "preview": "<encoded JSON up to 2 KB>",
          "persisted": True,
          "truncated": False,  # not lost — persisted in full
        }

    The LLM observation builder treats ``persisted: True`` as a hint
    to render a ``<persisted-output>`` marker rather than the bare
    summary. Downstream tools (``read_artifact``) accept the
    ``artifact_id`` to retrieve the full payload.
    """
    encoded = _safe_json_dumps(payload)
    if encoded is None:
        return None
    artifact_id = f"tool_result:{tool_name}:{uuid.uuid4().hex[:12]}"
    preview = _build_preview_text(encoded)
    ref = ContextArtifactRef(
        artifact_id=artifact_id,
        kind=ArtifactKind.TOOL_RESULT,
        size_bytes=len(encoded),
        sensitivity=SensitivityLevel.UNKNOWN,
        metadata={
            "tool_name": tool_name,
            "run_id": run_id or "",
            "tool_call_id": tool_call_id or "",
        },
    )
    stored = StoredArtifact(
        ref=ref,
        content=encoded,
        preview=preview,
        metadata={
            "tool_name": tool_name,
            "run_id": run_id or "",
        },
    )
    try:
        persisted_ref = store.put(stored)
    except Exception:
        logger.warning(
            "spill: artifact store rejected payload; falling back to "
            "legacy truncation (tool=%s, size=%d chars)",
            tool_name,
            len(encoded),
        )
        return None
    persisted_artifact = {
        "artifact_id": persisted_ref.artifact_id,
        "kind": ArtifactKind.TOOL_RESULT.value,
        "size_bytes": persisted_ref.size_bytes,
        "tool_name": tool_name,
    }
    workspace_path = _mirror_tool_result_to_workspace(
        encoded,
        artifact_id=persisted_ref.artifact_id,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
    )
    if workspace_path is not None:
        persisted_artifact["workspace_path"] = workspace_path
    replacement: dict[str, Any] = {
        "summary": (
            f"<persisted-output> {len(encoded):,} chars stored as "
            f"artifact {persisted_ref.artifact_id}; preview follows"
        ),
        "persisted_artifact": persisted_artifact,
        "preview": preview.text,
        "persisted": True,
        "truncated": False,
    }
    if workspace_path is not None:
        replacement["workspace_artifact_path"] = workspace_path
    return replacement, persisted_ref


def _mirror_tool_result_to_workspace(
    encoded: str,
    *,
    artifact_id: str,
    tool_name: str,
    tool_call_id: str | None,
) -> str | None:
    root = get_workspace_jail_root()
    if root is None:
        return None
    filename_seed = tool_call_id or artifact_id or tool_name or "tool_result"
    filename = f"{_safe_filename(filename_seed)}.json"
    target_dir = (root / "tool-results").resolve()
    target = (target_dir / filename).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    target_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(encoded, encoding="utf-8")
    return target.relative_to(root.resolve()).as_posix()


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())[:120].strip("._-")
    return cleaned or uuid.uuid4().hex[:12]


__all__ = [
    "PREVIEW_MAX_CHARS",
    "spill_payload_to_artifact",
    "should_spill_payload",
]
