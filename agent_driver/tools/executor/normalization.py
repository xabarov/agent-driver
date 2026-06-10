"""Planned-tool-call argument normalization for the governed executor.

Pure ``ToolCall`` / args-dict transforms (alias canonicalization, JSON-string
arg coercion, per-tool arg shaping) extracted from ``governed.py`` — no
dependency on the executor itself.
"""

from __future__ import annotations

import json
import re
from typing import Any

from agent_driver.contracts.tools import ToolCall

_TOOL_ALIASES: dict[str, str] = {
    "file_read": "read_file",
    "read": "read_file",
    "skill_search": "skill_tool",
    "web_search_tool": "web_search",
    "write": "file_write",
}
_URL_ARG_ALIASES = ("url", "uri", "href")
_PATH_ARG_ALIASES = ("path", "file_path", "filepath")


def _normalize_tool_alias(
    call: ToolCall, *, available_tool_names: tuple[str, ...]
) -> ToolCall:
    """Map common model-emitted tool synonyms onto registered tool names.

    Kept as a narrowly scoped compatibility hook. Obvious hallucinated aliases
    such as ``read_url`` should receive a corrective tool observation instead of
    silently executing another tool.
    """
    target_name = _TOOL_ALIASES.get(call.tool_name)
    if target_name is None or target_name not in available_tool_names:
        return _normalize_tool_call_args(call)
    if not _tool_alias_shape_matches(call, target_name):
        return _normalize_tool_call_args(call)
    args = _normalize_tool_args(target_name, call.args)
    return call.model_copy(
        update={
            "tool_name": target_name,
            "args": args,
            "metadata": {
                **call.metadata,
                "original_tool_name": call.tool_name,
                "tool_alias_normalized": True,
            },
        }
    )


def _tool_alias_shape_matches(call: ToolCall, target_name: str) -> bool:
    args = call.args
    if target_name == "read_file":
        return any(
            isinstance(args.get(key), str) and args[key].strip()
            for key in _PATH_ARG_ALIASES
        )
    if target_name == "file_write":
        has_path = any(
            isinstance(args.get(key), str) and args[key].strip()
            for key in _PATH_ARG_ALIASES
        )
        return has_path and isinstance(args.get("content"), str)
    return True


def _normalize_tool_call_args(call: ToolCall) -> ToolCall:
    normalized_args = _normalize_tool_args(call.tool_name, call.args)
    if normalized_args == call.args:
        return call
    return call.model_copy(
        update={
            "args": normalized_args,
            "metadata": {
                **call.metadata,
                "tool_args_normalized": True,
            },
        }
    )


def _coerce_json_string_args(args: dict[str, Any]) -> dict[str, Any]:
    """Parse args the model serialized as JSON STRINGS back into objects/arrays.

    Models (especially reasoning models) sometimes pass an object/array argument
    as a JSON string — e.g. ``chart_vegalite(spec='{"mark": "bar", ...}')`` instead
    of a native object — which then fails the handler's type check ("spec must be a
    dictionary"). When a string value cleanly parses to a dict/list (optionally
    inside a ```json fence), replace it with the parsed value. Generic — applies to
    every tool, so each handler doesn't need its own string-spec workaround.
    """
    coerced = dict(args)
    for key, value in args.items():
        if not isinstance(value, str):
            continue
        text = value.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
            text = re.sub(r"\s*```$", "", text).strip()
        if not (text.startswith("{") or text.startswith("[")):
            continue
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, (dict, list)):
            coerced[key] = parsed
    return coerced


def _normalize_tool_args(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Normalize common provider argument synonyms before handler execution."""
    normalized = _coerce_json_string_args(dict(args))
    if (
        tool_name in {"web_fetch", "source_read", "pdf_read", "browser_read"}
        and "url" not in normalized
    ):
        for key in _URL_ARG_ALIASES:
            value = normalized.get(key)
            if isinstance(value, str) and value.strip():
                normalized["url"] = value
                break
    if (
        tool_name
        in {
            "artifact_read",
            "file_edit",
            "file_patch",
            "file_write",
            "glob_search",
            "grep_search",
            "notebook_edit",
            "read_file",
        }
        and "path" not in normalized
    ):
        for key in _PATH_ARG_ALIASES:
            value = normalized.get(key)
            if isinstance(value, str) and value.strip():
                normalized["path"] = value
                break
    if tool_name == "agent_tool":
        _normalize_agent_tool_args(normalized)
    if tool_name in {"skill_tool", "skill_view"}:
        _normalize_skill_args(normalized)
    if tool_name == "todo_write":
        _normalize_todo_write_args(normalized)
    return normalized


def _normalize_agent_tool_args(args: dict[str, Any]) -> None:
    if "task" not in args:
        for key in ("instructions", "prompt", "query", "description"):
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                args["task"] = value
                break
    if "description" not in args:
        task = args.get("task")
        if isinstance(task, str) and task.strip():
            args["description"] = _short_description(task)
        else:
            args["description"] = "Delegated research task"
    args.setdefault("task_type", "research")
    args.setdefault("execution_mode", "sync")


def _normalize_skill_args(args: dict[str, Any]) -> None:
    trusted_roots = args.get("trusted_roots")
    if isinstance(trusted_roots, str):
        try:
            parsed = json.loads(trusted_roots)
        except json.JSONDecodeError:
            return
        if isinstance(parsed, list):
            args["trusted_roots"] = parsed


def _short_description(text: str) -> str:
    line = next((part.strip() for part in text.splitlines() if part.strip()), "")
    if not line:
        return "Delegated research task"
    return line[:96]


def _normalize_todo_write_args(args: dict[str, Any]) -> None:
    if "todos" not in args and "todo_items" in args:
        todo_items = args.get("todo_items")
        if isinstance(todo_items, str):
            try:
                todo_items = json.loads(todo_items)
            except json.JSONDecodeError:
                pass
        args["todos"] = todo_items
    elif isinstance(args.get("todos"), str):
        try:
            args["todos"] = json.loads(args["todos"])
        except json.JSONDecodeError:
            pass
    if "merge" not in args and "todo_merge" in args:
        merge = args.get("todo_merge")
        if isinstance(merge, str):
            args["merge"] = merge.strip().lower() in {"1", "true", "yes", "y"}
        else:
            args["merge"] = bool(merge)
    elif isinstance(args.get("merge"), str):
        args["merge"] = args["merge"].strip().lower() in {"1", "true", "yes", "y"}
