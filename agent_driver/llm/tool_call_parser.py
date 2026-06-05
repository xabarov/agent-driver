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
_ARG_PAIR_RE = re.compile(
    r"<arg_key>\s*(?P<key>[\s\S]*?)\s*</arg_key>\s*"
    r"<arg_value>\s*(?P<value>[\s\S]*?)\s*</arg_value>",
    re.IGNORECASE,
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
            tool_call_id=(
                call_id if isinstance(call_id, str) and call_id.strip() else None
            ),
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


def _xmlish_tool_call_payload(raw: str) -> dict[str, Any] | None:
    body = raw.strip()
    if not body or body.startswith("{"):
        return None
    first_tag = body.find("<")
    name_text = body[: first_tag if first_tag >= 0 else len(body)].strip()
    if not name_text:
        return None
    args: dict[str, Any] = {}
    for match in _ARG_PAIR_RE.finditer(body):
        key = match.group("key").strip()
        value = match.group("value").strip()
        if key:
            args[key] = _coerce_xmlish_arg_value(value)
    return {"name": name_text, "arguments": args}


def _coerce_xmlish_arg_value(value: str) -> Any:
    text = value.strip()
    if not text:
        return ""
    if text[0] in '[{"' or text in {"true", "false", "null"}:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return text


# Gemma chat-template tool calls leak as text when the provider doesn't parse them
# into structured tool_calls, e.g.:
#   <|tool_call>call:chart_vegalite{chart_type:<|"|>bar<|"|>,data:[...]}<tool_call|>
# markers (<|tool_call> / <tool_call|>; pipe placement varies) wrap a
# ``call:NAME{...}`` body whose string values are delimited by ``<|"|>``.
_GEMMA_CALL_RE = re.compile(r"call:\s*(?P<name>[A-Za-z0-9_]+)\s*\{")
# A gemma string-delimiter token wrapping a quote: <|"|>, <|">, <||">, ...
_GEMMA_QUOTE_RE = re.compile(r"<\|+\"\|*>")
# Any other stray gemma control token: <|tool_call>, <tool_call|>, <||>, <|...>.
_GEMMA_STRAY_RE = re.compile(r"<\|[^<>]*>|<[^<>]*\|>")
# Unquoted JSON object key after { or , — quote it so json.loads accepts it.
_BARE_KEY_RE = re.compile(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)")


def _gemma_tool_call_payloads(text: str) -> list[dict[str, Any]]:
    """Parse gemma ``call:NAME{...}`` leaks into ``{name, arguments}`` dicts.

    Best-effort: normalises gemma's ``<|"|>`` quote-tokens to ``"``, quotes bare
    object keys, then ``json.loads``. Pathological bodies (e.g. multi-line code
    with embedded quotes) may fail to parse — those are skipped, leaving behaviour
    no worse than before."""
    payloads: list[dict[str, Any]] = []
    for m in _GEMMA_CALL_RE.finditer(text):
        name = m.group("name")
        # Brace-match the args body.
        depth, i, n = 0, m.end() - 1, len(text)
        end = None
        while i < n:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
            i += 1
        if end is None:
            continue
        body = text[m.end() - 1 : end + 1]
        normalised = _GEMMA_QUOTE_RE.sub('"', body)
        normalised = _GEMMA_STRAY_RE.sub("", normalised)
        normalised = _BARE_KEY_RE.sub(r'\1"\2"\3', normalised)
        try:
            args = json.loads(normalised)
        except (ValueError, TypeError):
            continue
        if isinstance(args, dict):
            payloads.append({"name": name, "arguments": args})
    return payloads


def extract_text_form_tool_calls(
    text: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse fallback tool-call blocks from plain assistant text."""
    if not isinstance(text, str) or not text.strip():
        return [], []
    candidates: list[tuple[str, str]] = []
    xmlish_candidates: list[tuple[str, dict[str, Any]]] = []
    # Gemma <|tool_call>call:NAME{...} leaks (only when its markers are present, so
    # we don't mis-parse a normal ``call:`` mention in prose).
    if "<|tool_call" in text or "<tool_call|" in text:
        for payload in _gemma_tool_call_payloads(text):
            xmlish_candidates.append(("gemma_tool_call", payload))
    for match in _TOOL_CALL_BLOCK_RE.finditer(text):
        raw_body = match.group("body")
        xmlish = _xmlish_tool_call_payload(raw_body)
        if xmlish is not None:
            xmlish_candidates.append(("tool_call_xmlish_block", xmlish))
            continue
        body = _extract_json_object(raw_body)
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
    if not candidates and not xmlish_candidates:
        return [], []
    planned: list[dict[str, Any]] = []
    parse_errors: list[dict[str, Any]] = []
    index = 0
    for source, payload in xmlish_candidates:
        parsed, parse_error = _to_tool_call(payload, index=index, source=source)
        if parsed is not None:
            planned.append(parsed)
        if parse_error is not None:
            parse_errors.append(parse_error)
        index += 1
    for source, raw_payload in candidates:
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
