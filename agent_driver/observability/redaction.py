"""Observer payload sanitization: bounded + secret-redacted (epic 037 phase C).

Reference-first port of hermes ``run_agent.py::_sanitize_hook_payload`` /
``_hook_jsonable`` / ``_is_sensitive_hook_key``. An observer payload that leaves
the process (into a trace store or a host subscriber) must be (a) BOUNDED so a
runaway structure can't blow up the exporter, and (b) SECRET-SAFE so an auth
header or API key never lands in telemetry. This is the engine-side seam a host
plugs its own PII redaction into BEFORE export (MeetScript's raw-free policy).

Deliberately NOT a free-text scrubber (vendor-key prefixes, JWTs, phone numbers)
— that is log/message redaction and lives in ``llm/sanitize.py`` +
``context/compaction/sanitizers.py``. Here we bound structure and mask values
under sensitive KEYS, which is the right scope for a structured hook payload.
"""

from __future__ import annotations

from typing import Any

from agent_driver.contracts.artifacts import RedactionInfo
from agent_driver.contracts.enums import SensitivityLevel

# Bounds mirror hermes ``_hook_jsonable`` defaults.
MAX_DEPTH = 8
MAX_STRING = 8000
MAX_SEQUENCE = 200

_REDACTED = "<redacted>"
_REDACTION_POLICY = "observer.v1/sensitive-key"

# hermes ``_is_sensitive_hook_key``: exact matches + ``*_api_key`` suffix. Exact
# (not substring) so ``token_count`` / ``session_id`` are NOT masked.
_SENSITIVE_EXACT = frozenset(
    {
        "api_key",
        "authorization",
        "proxy_authorization",
        "cookie",
        "set_cookie",
        "secret",
        "password",
        "token",
        "access_token",
        "refresh_token",
        "private_key",
    }
)


def is_sensitive_hook_key(key: str) -> bool:
    """Whether a mapping key names a secret whose VALUE must be masked."""
    lowered = key.lower().replace("-", "_")
    return lowered in _SENSITIVE_EXACT or lowered.endswith("_api_key")


def sanitize_observer_payload(
    value: Any,
    *,
    max_depth: int = MAX_DEPTH,
    max_string: int = MAX_STRING,
    max_sequence: int = MAX_SEQUENCE,
) -> tuple[Any, RedactionInfo]:
    """Return a bounded, secret-masked, JSON-safe copy of ``value`` + a report.

    The :class:`RedactionInfo` records whether anything was masked/truncated and
    the sensitive field names that were masked (names only — never values), so a
    :class:`~agent_driver.contracts.events.RuntimeEvent` can carry an honest
    ``redaction`` descriptor. Never raises: a non-serializable leaf degrades to
    ``str()`` / ``repr()`` rather than failing the run.
    """
    state = {"redacted_fields": [], "truncated": False, "masked": False}
    cleaned = _jsonable(
        value,
        depth=0,
        max_depth=max_depth,
        max_string=max_string,
        max_sequence=max_sequence,
        state=state,
    )
    fields = sorted(set(state["redacted_fields"]))
    applied = bool(state["masked"] or state["truncated"])
    info = RedactionInfo(
        applied=applied,
        policy=_REDACTION_POLICY if applied else None,
        redacted_fields=fields,
        sensitivity=(
            SensitivityLevel.CONFIDENTIAL
            if state["masked"]
            else SensitivityLevel.INTERNAL
        ),
        metadata={"truncated": state["truncated"]},
    )
    return cleaned, info


def _jsonable(
    value: Any,
    *,
    depth: int,
    max_depth: int,
    max_string: int,
    max_sequence: int,
    state: dict[str, Any],
) -> Any:
    if depth >= max_depth:
        state["truncated"] = True
        return "<max-depth>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > max_string:
            state["truncated"] = True
            return (
                value[:max_string] + f"...[truncated {len(value) - max_string} chars]"
            )
        return value
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_sequence:
                state["truncated"] = True
                out["_truncated_items"] = len(value) - max_sequence
                break
            key_str = str(key)
            if is_sensitive_hook_key(key_str):
                state["masked"] = True
                state["redacted_fields"].append(key_str)
                out[key_str] = _REDACTED
                continue
            out[key_str] = _jsonable(
                item,
                depth=depth + 1,
                max_depth=max_depth,
                max_string=max_string,
                max_sequence=max_sequence,
                state=state,
            )
        return out
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        out_list: list[Any] = []
        for index, item in enumerate(items):
            if index >= max_sequence:
                state["truncated"] = True
                out_list.append({"_truncated_items": len(items) - max_sequence})
                break
            out_list.append(
                _jsonable(
                    item,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_string=max_string,
                    max_sequence=max_sequence,
                    state=state,
                )
            )
        return out_list
    # Normalize arbitrary objects: pydantic → dataclass-ish → str fallback.
    dumped = getattr(value, "model_dump", None)
    if callable(dumped):
        try:
            return _jsonable(
                value.model_dump(mode="json"),
                depth=depth + 1,
                max_depth=max_depth,
                max_string=max_string,
                max_sequence=max_sequence,
                state=state,
            )
        except Exception:  # noqa: BLE001 - fall through to str()
            pass
    try:
        return _jsonable(
            str(value),
            depth=depth + 1,
            max_depth=max_depth,
            max_string=max_string,
            max_sequence=max_sequence,
            state=state,
        )
    except Exception:  # noqa: BLE001 - last resort
        return "<unserializable>"


__all__ = [
    "MAX_DEPTH",
    "MAX_STRING",
    "MAX_SEQUENCE",
    "is_sensitive_hook_key",
    "sanitize_observer_payload",
]
