"""Fallback parser for text-form tool calls from non-native providers."""

from __future__ import annotations

import json
import re
from typing import Any

from agent_driver.contracts.tools import ToolCall

_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*?\}")
_TOOL_CALL_BLOCK_RE = re.compile(
    r"<tool_call>\s*(?P<body>[\s\S]*?)\s*</tool_call>", re.IGNORECASE
)
_PYTHON_TAG_BLOCK_RE = re.compile(
    r"<\|python_tag\|>\s*(?P<body>[\s\S]*?)\s*<\|eom_id\|>", re.IGNORECASE
)
_TOOL_CALL_FENCE_RE = re.compile(
    r"(?:^|\n)\s*(?:<tool_call>|tool_call:)\s*```(?:json)?\s*(?P<body>[\s\S]*?)\s*```",
    re.IGNORECASE,
)


def _extract_json_object(raw: str) -> str | None:
    stripped = raw.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    match = _JSON_OBJECT_RE.search(stripped)
    if match is None:
        return None
    return match.group(0).strip()


def _to_tool_call(
    payload: dict[str, Any], *, index: int, source: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    name = str(payload.get("name") or payload.get("tool_name") or "").strip()
    if not name:
        return None, {
            "index": index,
            "source": source,
            "error": "missing_tool_name",
        }
    raw_args = payload.get("arguments", payload.get("parameters", {}))
    args: dict[str, Any] = {}
    if isinstance(raw_args, dict):
        args = raw_args
    elif isinstance(raw_args, str):
        text = raw_args.strip()
        if text:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return None, {
                    "index": index,
                    "source": source,
                    "tool_name": name,
                    "error": "arguments_json_parse_failed",
                    "raw_arguments": text,
                }
            if isinstance(parsed, dict):
                args = parsed
            else:
                return None, {
                    "index": index,
                    "source": source,
                    "tool_name": name,
                    "error": "arguments_json_must_be_object",
                    "raw_arguments": text,
                }
    call_id = payload.get("id")
    try:
        tool_call = ToolCall(
            tool_name=name,
            args=args,
            tool_call_id=call_id if isinstance(call_id, str) and call_id.strip() else None,
            metadata={"text_form_source": source, "text_form_index": index},
        )
    except (TypeError, ValueError):
        return None, {
            "index": index,
            "source": source,
            "tool_name": name,
            "error": "tool_call_validation_failed",
        }
    return tool_call.model_dump(mode="json"), None


def extract_text_form_tool_calls(
    text: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse fallback tool-call blocks from plain assistant text."""
    if not isinstance(text, str) or not text.strip():
        return [], []
    candidates: list[tuple[str, str]] = []
    for match in _TOOL_CALL_BLOCK_RE.finditer(text):
        body = _extract_json_object(match.group("body"))
        if body:
            candidates.append(("tool_call_block", body))
    for match in _PYTHON_TAG_BLOCK_RE.finditer(text):
        body = _extract_json_object(match.group("body"))
        if body:
            candidates.append(("python_tag_block", body))
    for match in _TOOL_CALL_FENCE_RE.finditer(text):
        body = _extract_json_object(match.group("body"))
        if body:
            candidates.append(("tool_call_fence", body))
    if not candidates:
        return [], []
    planned: list[dict[str, Any]] = []
    parse_errors: list[dict[str, Any]] = []
    for index, (source, raw_payload) in enumerate(candidates):
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            parse_errors.append(
                {
                    "index": index,
                    "source": source,
                    "error": "payload_json_parse_failed",
                    "raw_payload": raw_payload,
                }
            )
            continue
        if not isinstance(payload, dict):
            parse_errors.append(
                {
                    "index": index,
                    "source": source,
                    "error": "payload_json_must_be_object",
                    "raw_payload": raw_payload,
                }
            )
            continue
        parsed, parse_error = _to_tool_call(payload, index=index, source=source)
        if parsed is not None:
            planned.append(parsed)
        if parse_error is not None:
            parse_errors.append(parse_error)
    return planned, parse_errors


def strip_text_form_tool_calls(text: str) -> str:
    """Remove plain-text tool call blocks from assistant transcript content."""
    if not isinstance(text, str) or not text.strip():
        return text
    stripped = _TOOL_CALL_BLOCK_RE.sub("", text)
    stripped = _PYTHON_TAG_BLOCK_RE.sub("", stripped)
    stripped = _TOOL_CALL_FENCE_RE.sub("", stripped)
    return stripped.strip()


__all__ = ["extract_text_form_tool_calls", "strip_text_form_tool_calls"]
