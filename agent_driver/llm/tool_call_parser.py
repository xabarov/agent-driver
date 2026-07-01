"""Fallback parser for text-form tool calls from non-native providers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
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


@dataclass(slots=True)
class TextFormToolCallParseResult:
    """Parsed fallback tool calls plus source ranges in the original text."""

    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    parse_errors: list[dict[str, Any]] = field(default_factory=list)
    ranges: list[dict[str, Any]] = field(default_factory=list)


def _range_record(
    *,
    start: int,
    end: int,
    source: str,
    index: int,
    accepted: bool,
    tool_name: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "start": start,
        "end": end,
        "source": source,
        "index": index,
        "accepted": accepted,
    }
    if tool_name:
        record["tool_name"] = tool_name
    if error:
        record["error"] = error
    return record


def _tool_call_dedupe_key(tool_call: dict[str, Any]) -> str:
    payload = {
        "tool_name": tool_call.get("tool_name"),
        "args": (
            tool_call.get("args") if isinstance(tool_call.get("args"), dict) else {}
        ),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _append_unique_tool_call(
    *,
    planned: list[dict[str, Any]],
    seen: set[str],
    parsed: dict[str, Any],
) -> bool:
    key = _tool_call_dedupe_key(parsed)
    if key in seen:
        return False
    seen.add(key)
    planned.append(parsed)
    return True


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
    repair_metadata: dict[str, Any] = {}
    if isinstance(raw_args, dict):
        args = raw_args
    elif isinstance(raw_args, str):
        text = raw_args.strip()
        if text:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                repaired = repair_tool_call_arguments_json(text)
                if repaired is None:
                    return None, {
                        "index": index,
                        "source": source,
                        "tool_name": name,
                        "error": "arguments_json_parse_failed",
                        "raw_arguments": text,
                    }
                parsed = repaired["arguments"]
                repair_metadata = {
                    "text_form_args_repaired": True,
                    "text_form_args_repair_reasons": repaired["repairs"],
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
            metadata={
                "text_form_source": source,
                "text_form_index": index,
                **repair_metadata,
            },
        )
    except (TypeError, ValueError):
        return None, {
            "index": index,
            "source": source,
            "tool_name": name,
            "error": "tool_call_validation_failed",
        }
    return tool_call.model_dump(mode="json"), None


def repair_tool_call_arguments_json(raw: str) -> dict[str, Any] | None:
    """Repair common truncated/malformed JSON object argument strings.

    Returns ``{"arguments": dict, "repaired_text": str, "repairs": list[str]}``
    on success. The helper intentionally only accepts a repaired JSON object;
    unrepairable input stays a parse error so the runtime can ask the model to
    retry instead of executing surprising arguments.
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return {"arguments": {}, "repaired_text": "{}", "repairs": ["empty_arguments"]}
    repairs: list[str] = []
    first_obj = text.find("{")
    if first_obj > 0:
        text = text[first_obj:]
        repairs.append("trimmed_prefix")
    if any(ord(ch) < 32 and ch not in "\t\n\r" for ch in text):
        text = "".join(
            " " if ord(ch) < 32 and ch not in "\t\n\r" else ch for ch in text
        )
        repairs.append("unescaped_control_chars")
    replaced = re.sub(r"\bNone\b", "null", text)
    replaced = re.sub(r"\bTrue\b", "true", replaced)
    replaced = re.sub(r"\bFalse\b", "false", replaced)
    if replaced != text:
        text = replaced
        repairs.append("python_literals")
    trimmed_commas = re.sub(r",\s*([}\]])", r"\1", text)
    if trimmed_commas != text:
        text = trimmed_commas
        repairs.append("trailing_commas")
    text, extra_closer_repairs = _trim_extra_json_closers(text)
    repairs.extend(extra_closer_repairs)
    text, balance_repairs = _balance_truncated_json(text)
    repairs.extend(balance_repairs)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return {
        "arguments": parsed,
        "repaired_text": text,
        "repairs": repairs or ["json_normalized"],
    }


def _trim_extra_json_closers(text: str) -> tuple[str, list[str]]:
    repairs: list[str] = []
    while text and text[-1] in "}]":
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            candidate = text[:-1].rstrip()
            try:
                json.loads(candidate)
            except json.JSONDecodeError:
                break
            text = candidate
            repairs.append("extra_closing_delimiter")
            continue
        if isinstance(parsed, dict):
            break
        break
    return text, repairs


def _balance_truncated_json(text: str) -> tuple[str, list[str]]:
    stack: list[str] = []
    in_string: str | None = None
    escaped = False
    for ch in text:
        if in_string is not None:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == in_string:
                in_string = None
            continue
        if ch in ('"', "'"):
            in_string = ch
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if stack and stack[-1] == ch:
                stack.pop()
    repairs: list[str] = []
    if in_string is not None:
        text += '"'
        repairs.append("closed_truncated_string")
    if stack:
        text += "".join(reversed(stack))
        repairs.append("balanced_truncated_object")
    return text, repairs


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
    return [payload for _start, _end, payload in _gemma_tool_call_candidates(text)]


def _gemma_tool_call_candidates(text: str) -> list[tuple[int, int, dict[str, Any]]]:
    """Parse gemma ``call:NAME{...}`` leaks into ``{name, arguments}`` dicts.

    Best-effort: normalises gemma's ``<|"|>`` quote-tokens to ``"``, quotes bare
    object keys, then ``json.loads``. Pathological bodies (e.g. multi-line code
    with embedded quotes) may fail to parse — those are skipped, leaving behaviour
    no worse than before."""
    payloads: list[tuple[int, int, dict[str, Any]]] = []
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
            start = m.start()
            marker_start = text.rfind("<", 0, start)
            if marker_start >= 0 and "tool_call" in text[marker_start:start]:
                start = marker_start
            end_idx = end + 1
            marker_end = text.find(">", end_idx)
            if marker_end >= 0 and "tool_call" in text[end_idx : marker_end + 1]:
                end_idx = marker_end + 1
            payloads.append((start, end_idx, {"name": name, "arguments": args}))
    return payloads


# DeepSeek v4 emits tool calls in a Claude-style invoke/parameter XML wrapped in
# fullwidth "DSML" markers when the provider doesn't parse them into native
# tool_calls. ``｜`` is U+FF5C (FULLWIDTH VERTICAL LINE), doubled:
#   <｜｜DSML｜｜tool_calls>
#     <｜｜DSML｜｜invoke name="excel_set_cell">
#       <｜｜DSML｜｜parameter name="sheet_name" string="true">Sales</｜｜DSML｜｜parameter>
#       <｜｜DSML｜｜parameter name="value" string="false">1420</｜｜DSML｜｜parameter>
#     </｜｜DSML｜｜invoke>
#   </｜｜DSML｜｜tool_calls>
# ``string="false"`` flags a non-string value (number/array/bool) → JSON-parse.
#
# Pipe tolerance: the canonical marker uses U+FF5C (FULLWIDTH VERTICAL LINE) but
# the same tool-call leak is observed with ASCII ``|`` pipes and with whitespace
# around the pipes/word (e.g. ``< | DSML | tool_calls>``) depending on the
# provider/proxy and how the text is re-encoded. Accept any mix of ``｜``/``|``
# plus optional surrounding whitespace so the leak is parsed (→ executed) instead
# of leaking into the answer. Safe: every entry point is gated on the literal
# ``"DSML"`` being present, so prose can't false-match.
_DSML_PIPES = r"[｜|]+\s*"
_DSML_OPEN = r"<\s*" + _DSML_PIPES + r"DSML\s*" + _DSML_PIPES
_DSML_CLOSE = r"</\s*" + _DSML_PIPES + r"DSML\s*" + _DSML_PIPES
_DSML_INVOKE_RE = re.compile(
    _DSML_OPEN + r"invoke\s+name=\"(?P<name>[^\"]+)\"\s*>"
    r"(?P<body>[\s\S]*?)" + _DSML_CLOSE + r"invoke>"
)
_DSML_PARAM_RE = re.compile(
    _DSML_OPEN + r"parameter\s+name=\"(?P<key>[^\"]+)\"(?P<attrs>[^>]*)>"
    r"(?P<value>[\s\S]*?)" + _DSML_CLOSE + r"parameter>"
)
_DSML_BLOCK_RE = re.compile(
    _DSML_OPEN + r"tool_calls>[\s\S]*?" + _DSML_CLOSE + r"tool_calls>"
)
# Any leftover stray DSML marker token, open or close: <｜｜DSML｜｜...> / </｜｜DSML｜｜...>
# (fullwidth or ASCII pipes, optional whitespace — see _DSML_PIPES rationale).
_DSML_STRAY_RE = re.compile(
    r"</?\s*" + _DSML_PIPES + r"DSML\s*" + _DSML_PIPES + r"[^>]*>"
)


def _coerce_dsml_value(value: str, *, is_string: bool) -> Any:
    text = value.strip()
    if is_string:
        return text
    # Non-string (number / array / bool / null) — JSON-parse, else heuristics.
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return _coerce_xmlish_arg_value(text)


def _dsml_tool_call_payloads(text: str) -> list[dict[str, Any]]:
    return [payload for _start, _end, payload in _dsml_tool_call_candidates(text)]


def _dsml_tool_call_candidates(text: str) -> list[tuple[int, int, dict[str, Any]]]:
    """Parse deepseek ``<｜｜DSML｜｜invoke…>`` leaks into ``{name, arguments}`` dicts.

    Best-effort: each ``invoke`` block contributes one tool call; its
    ``parameter`` children become args, with ``string="false"`` values JSON-parsed
    (numbers, arrays like ``[[2070],[600]]``). Malformed values fall back to the
    xmlish coercion heuristic, never raising."""
    payloads: list[tuple[int, int, dict[str, Any]]] = []
    block_ranges = [(m.start(), m.end()) for m in _DSML_BLOCK_RE.finditer(text)]
    for m in _DSML_INVOKE_RE.finditer(text):
        name = m.group("name").strip()
        if not name:
            continue
        args: dict[str, Any] = {}
        for pm in _DSML_PARAM_RE.finditer(m.group("body")):
            key = pm.group("key").strip()
            if not key:
                continue
            is_string = 'string="true"' in (pm.group("attrs") or "")
            args[key] = _coerce_dsml_value(pm.group("value"), is_string=is_string)
        start, end = m.start(), m.end()
        for block_start, block_end in block_ranges:
            if block_start <= start and end <= block_end:
                start, end = block_start, block_end
                break
        payloads.append((start, end, {"name": name, "arguments": args}))
    return payloads


def extract_text_form_tool_call_details(text: str) -> TextFormToolCallParseResult:
    """Parse fallback tool-call blocks and return source ranges.

    ``extract_text_form_tool_calls`` keeps the historic two-list return shape.
    This richer helper is for stream/render layers that need to hide exactly the
    accepted fallback markup while preserving prose around it.
    """
    if not isinstance(text, str) or not text.strip():
        return TextFormToolCallParseResult()
    candidates: list[tuple[str, str, int, int]] = []
    xmlish_candidates: list[tuple[str, dict[str, Any], int, int]] = []
    # Gemma <|tool_call>call:NAME{...} leaks (only when its markers are present, so
    # we don't mis-parse a normal ``call:`` mention in prose).
    if "<|tool_call" in text or "<tool_call|" in text:
        for start, end, payload in _gemma_tool_call_candidates(text):
            xmlish_candidates.append(("gemma_tool_call", payload, start, end))
    # DeepSeek v4 <｜｜DSML｜｜invoke…> leaks (gated on the DSML marker).
    if "DSML" in text:
        for start, end, payload in _dsml_tool_call_candidates(text):
            xmlish_candidates.append(("deepseek_dsml", payload, start, end))
    for match in _TOOL_CALL_BLOCK_RE.finditer(text):
        raw_body = match.group("body")
        xmlish = _xmlish_tool_call_payload(raw_body)
        if xmlish is not None:
            xmlish_candidates.append(
                ("tool_call_xmlish_block", xmlish, match.start(), match.end())
            )
            continue
        body = _extract_json_object(raw_body)
        if body:
            candidates.append(("tool_call_block", body, match.start(), match.end()))
    for match in _PYTHON_TAG_BLOCK_RE.finditer(text):
        body = _extract_json_object(match.group("body"))
        if body:
            candidates.append(("python_tag_block", body, match.start(), match.end()))
    for match in _TOOL_CALL_FENCE_RE.finditer(text):
        body = _extract_json_object(match.group("body"))
        if body:
            candidates.append(("tool_call_fence", body, match.start(), match.end()))
    if not candidates and not xmlish_candidates:
        return TextFormToolCallParseResult()
    planned: list[dict[str, Any]] = []
    parse_errors: list[dict[str, Any]] = []
    ranges: list[dict[str, Any]] = []
    seen_tool_calls: set[str] = set()
    index = 0
    for source, payload, start, end in xmlish_candidates:
        parsed, parse_error = _to_tool_call(payload, index=index, source=source)
        if parsed is not None:
            _append_unique_tool_call(
                planned=planned,
                seen=seen_tool_calls,
                parsed=parsed,
            )
            ranges.append(
                _range_record(
                    start=start,
                    end=end,
                    source=source,
                    index=index,
                    accepted=True,
                    tool_name=parsed.get("tool_name"),
                )
            )
        if parse_error is not None:
            parse_errors.append(parse_error)
            ranges.append(
                _range_record(
                    start=start,
                    end=end,
                    source=source,
                    index=index,
                    accepted=False,
                    tool_name=parse_error.get("tool_name"),
                    error=parse_error.get("error"),
                )
            )
        index += 1
    for source, raw_payload, start, end in candidates:
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            error = {
                "index": index,
                "source": source,
                "error": "payload_json_parse_failed",
                "raw_payload": raw_payload,
            }
            parse_errors.append(error)
            ranges.append(
                _range_record(
                    start=start,
                    end=end,
                    source=source,
                    index=index,
                    accepted=False,
                    error=error["error"],
                )
            )
            continue
        if not isinstance(payload, dict):
            error = {
                "index": index,
                "source": source,
                "error": "payload_json_must_be_object",
                "raw_payload": raw_payload,
            }
            parse_errors.append(error)
            ranges.append(
                _range_record(
                    start=start,
                    end=end,
                    source=source,
                    index=index,
                    accepted=False,
                    error=error["error"],
                )
            )
            continue
        parsed, parse_error = _to_tool_call(payload, index=index, source=source)
        if parsed is not None:
            _append_unique_tool_call(
                planned=planned,
                seen=seen_tool_calls,
                parsed=parsed,
            )
            ranges.append(
                _range_record(
                    start=start,
                    end=end,
                    source=source,
                    index=index,
                    accepted=True,
                    tool_name=parsed.get("tool_name"),
                )
            )
        if parse_error is not None:
            parse_errors.append(parse_error)
            ranges.append(
                _range_record(
                    start=start,
                    end=end,
                    source=source,
                    index=index,
                    accepted=False,
                    tool_name=parse_error.get("tool_name"),
                    error=parse_error.get("error"),
                )
            )
        index += 1
    return TextFormToolCallParseResult(
        tool_calls=planned, parse_errors=parse_errors, ranges=ranges
    )


def extract_text_form_tool_calls(
    text: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse fallback tool-call blocks from plain assistant text."""
    result = extract_text_form_tool_call_details(text)
    return result.tool_calls, result.parse_errors


def strip_text_form_tool_call_ranges(
    text: str,
    ranges: list[dict[str, Any]],
    *,
    accepted_only: bool = False,
) -> str:
    """Remove parser source ranges from ``text``.

    Ranges may overlap (e.g. several DSML invokes inside one wrapper). They are
    merged before stripping so callers can safely pass the raw parser metadata.
    """
    if not text or not ranges:
        return text
    spans: list[tuple[int, int]] = []
    for item in ranges:
        if accepted_only and item.get("accepted") is not True:
            continue
        try:
            start = int(item.get("start"))
            end = int(item.get("end"))
        except (TypeError, ValueError):
            continue
        if 0 <= start < end <= len(text):
            spans.append((start, end))
    if not spans:
        return text
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    parts: list[str] = []
    cursor = 0
    for start, end in merged:
        parts.append(text[cursor:start])
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts).strip()


def strip_text_form_tool_calls(text: str) -> str:
    """Remove plain-text tool call blocks from assistant transcript content."""
    if not isinstance(text, str) or not text.strip():
        return text
    stripped = _TOOL_CALL_BLOCK_RE.sub("", text)
    stripped = _PYTHON_TAG_BLOCK_RE.sub("", stripped)
    stripped = _TOOL_CALL_FENCE_RE.sub("", stripped)
    # DeepSeek DSML: drop the whole tool_calls block, any stray invoke blocks,
    # then any leftover DSML marker tokens (truncated/unclosed wrappers).
    stripped = _DSML_BLOCK_RE.sub("", stripped)
    stripped = _DSML_INVOKE_RE.sub("", stripped)
    stripped = _DSML_STRAY_RE.sub("", stripped)
    return stripped.strip()


__all__ = [
    "TextFormToolCallParseResult",
    "extract_text_form_tool_call_details",
    "extract_text_form_tool_calls",
    "repair_tool_call_arguments_json",
    "strip_text_form_tool_call_ranges",
    "strip_text_form_tool_calls",
]
