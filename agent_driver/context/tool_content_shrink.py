"""Structure-preserving shrink of a (possibly-JSON) tool message content.

Several context passes shorten a TOOL message whose content is a serialized JSON
object — ``json.dumps`` of a tool's ``structured_output`` (built at
``tool_stage/__init__.py``). A raw ``content[:N]`` slice cuts that JSON mid-structure,
so any consumer that reads tool-result content as strict JSON (an external / host
parser, or a provider validating the replayed transcript) gets malformed data.
Reference: hermes ``_truncate_tool_call_args_json`` — its byte-slice predecessor
produced unterminated strings / missing braces that drew non-retryable provider 400s
and looped the session re-sending broken history.

``shrink_json_tool_content`` mirrors that fix: parse the JSON and truncate only its
long STRING leaves in-tree (with an inline marker), re-serializing to still-valid JSON.
The object/array shape and all keys survive by construction. It returns ``None`` when
the content is not a JSON object/array, so the caller falls back to its own plain-text
truncation (a char-slice of prose is safe). Idempotent: an already-shrunk value comes
back unchanged.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Default per-string-leaf head budget (chars). Mirrors hermes head_chars≈200: long
# string values are the bulk of an oversized JSON tool result; keys/scalars/shape stay.
DEFAULT_LEAF_HEAD_CHARS = 256

_LEAF_MARKER = "…[+{dropped} chars]"
_LEAF_MARKER_RE = re.compile(r"…\[\+\d+ chars\]$")


def _shrink_value(value: Any, head_chars: int) -> tuple[Any, bool]:
    """Recursively truncate long string leaves; return (new_value, changed)."""
    if isinstance(value, str):
        if len(value) > head_chars and not _LEAF_MARKER_RE.search(value):
            dropped = len(value) - head_chars
            return value[:head_chars] + _LEAF_MARKER.format(dropped=dropped), True
        return value, False
    if isinstance(value, dict):
        changed = False
        out: dict[Any, Any] = {}
        for key, item in value.items():
            new_item, item_changed = _shrink_value(item, head_chars)
            out[key] = new_item
            changed = changed or item_changed
        return out, changed
    if isinstance(value, list):
        changed = False
        out_list: list[Any] = []
        for item in value:
            new_item, item_changed = _shrink_value(item, head_chars)
            out_list.append(new_item)
            changed = changed or item_changed
        return out_list, changed
    return value, False


def shrink_json_tool_content(
    content: str,
    *,
    leaf_head_chars: int = DEFAULT_LEAF_HEAD_CHARS,
) -> str | None:
    """Structure-preserving shrink of a serialized-JSON tool result.

    Returns valid re-serialized JSON with long string leaves truncated in-tree; the
    input unchanged when there is nothing left to shrink (idempotent); or ``None`` when
    ``content`` is not a JSON object/array (caller does its own plain-text truncation).
    """
    if not content:
        return None
    if content.lstrip()[:1] not in ("{", "["):
        return None
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, (dict, list)):
        return None
    shrunk, changed = _shrink_value(parsed, leaf_head_chars)
    if not changed:
        return content  # valid JSON, nothing over the leaf budget — keep as-is
    return json.dumps(shrunk, ensure_ascii=True)


__all__ = ["DEFAULT_LEAF_HEAD_CHARS", "shrink_json_tool_content"]
