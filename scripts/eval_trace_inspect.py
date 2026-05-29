#!/usr/bin/env python3
"""Inspect one eval scenario artifact with detailed event/tool timeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _short(value: Any, *, limit: int) -> str:
    rendered = json.dumps(value, ensure_ascii=True, sort_keys=True)
    if len(rendered) <= limit:
        return rendered
    return rendered[: max(0, limit - 3)] + "..."


def _extract_tool_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    tools = payload.get("tools")
    if isinstance(tools, list):
        return [row for row in tools if isinstance(row, dict)]
    return []


def _extract_usage(payload: dict[str, Any]) -> dict[str, Any]:
    usage = payload.get("usage")
    if isinstance(usage, dict):
        return usage
    return {}


def _consecutive_repeats(chain: list[str]) -> list[tuple[str, int]]:
    """Return tools repeated back-to-back with repeat count (e.g. read_file x3)."""
    if not chain:
        return []
    repeats: list[tuple[str, int]] = []
    current = chain[0]
    streak = 1
    for item in chain[1:]:
        if item == current:
            streak += 1
            continue
        if streak > 1:
            repeats.append((current, streak))
        current = item
        streak = 1
    if streak > 1:
        repeats.append((current, streak))
    return repeats


def _is_subsequence(expected: list[str], actual: list[str]) -> bool:
    if not expected:
        return True
    index = 0
    for item in actual:
        if item == expected[index]:
            index += 1
            if index >= len(expected):
                return True
    return False


def _render_timeline(
    artifact: dict[str, Any], *, max_args: int, max_result: int
) -> tuple[list[str], list[str]]:
    lines: list[str] = []
    actual_chain: list[str] = []
    run_output = artifact.get("run_output")
    events = run_output.get("events") if isinstance(run_output, dict) else []
    if not isinstance(events, list):
        events = []
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "")
        seq = event.get("seq")
        payload = event.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        prefix = f"event[{seq}] {event_type}>"
        if event_type == "llm_call_started":
            provider = payload.get("provider")
            model = payload.get("model")
            lines.append(f"{prefix} provider={provider} model={model}")
            continue
        if event_type == "llm_call_completed":
            provider = payload.get("provider")
            model = payload.get("model")
            finish = payload.get("finish_reason")
            usage = _extract_usage(payload)
            prompt_t = usage.get("prompt_tokens")
            completion_t = usage.get("completion_tokens")
            lines.append(
                f"{prefix} provider={provider} model={model} finish={finish} "
                f"prompt_tokens={prompt_t} completion_tokens={completion_t}"
            )
            continue
        if event_type in {"tool_call_started", "tool_call_completed"}:
            for tool in _extract_tool_rows(payload):
                name = str(tool.get("tool_name") or "?")
                actual_chain.append(name)
                status = tool.get("status")
                duration_ms = tool.get("duration_ms")
                error_code = tool.get("error_code")
                metadata = tool.get("metadata")
                args = {}
                if isinstance(metadata, dict):
                    candidate = metadata.get("args")
                    if isinstance(candidate, dict):
                        args = candidate
                if not args:
                    candidate = tool.get("args_summary")
                    if isinstance(candidate, dict):
                        args = candidate
                result = tool.get("result_summary")
                lines.append(
                    f"{prefix} tool={name} status={status} duration_ms={duration_ms} "
                    f"error_code={error_code} args={_short(args, limit=max_args)} "
                    f"result={_short(result, limit=max_result)}"
                )
            continue
        if event_type == "warning":
            kind = payload.get("kind")
            reason = payload.get("reason")
            lines.append(f"{prefix} kind={kind} reason={reason}")
            continue
        if event_type == "interrupt_requested":
            reason = payload.get("reason")
            lines.append(f"{prefix} reason={reason}")
            continue
    return lines, actual_chain


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect one eval trace artifact.")
    parser.add_argument("artifact_json", help="Path to <scenario>.json artifact")
    parser.add_argument(
        "--max-args",
        type=int,
        default=200,
        help="Max rendered characters for tool args payload",
    )
    parser.add_argument(
        "--max-result",
        type=int,
        default=400,
        help="Max rendered characters for tool result payload",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    path = Path(args.artifact_json)
    if not path.exists():
        print(f"error: missing artifact file {path}")
        return 2
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print("error: artifact file is not valid JSON")
        return 2
    if not isinstance(artifact, dict):
        print("error: artifact JSON must be object")
        return 2
    scenario = artifact.get("scenario") if isinstance(artifact.get("scenario"), dict) else {}
    summary = artifact.get("summary") if isinstance(artifact.get("summary"), dict) else {}
    lines, actual_chain = _render_timeline(
        artifact, max_args=args.max_args, max_result=args.max_result
    )
    expected_chain = scenario.get("expected_tool_chain_contains")
    expected_chain = expected_chain if isinstance(expected_chain, list) else []
    final_answer = str(artifact.get("final_answer") or "")
    print(f"scenario> {scenario.get('scenario_id')}")
    print(
        f"status> {summary.get('status')} terminal_reason={summary.get('terminal_reason')} "
        f"runtime_step_count={summary.get('runtime_step_count')}"
    )
    for line in lines:
        print(line)
    print(f"actual_chain> {actual_chain}")
    repeats = _consecutive_repeats(actual_chain)
    if repeats:
        print(f"consecutive_repeats> {repeats}")
    print(f"expected_chain> {expected_chain}")
    print(f"expected_chain_satisfied> {_is_subsequence(expected_chain, actual_chain)}")
    print(f"expected_tools_missing> {summary.get('expected_tools_missing')}")
    print(f"final_answer_preview> {final_answer[:500]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
