"""Answer-expectation matching and bug-tag scoring for evals."""

from __future__ import annotations

import re
from typing import Any

from .eval_scenarios import EvalScenario


def _answer_matches_expectations(*, answer: str, scenario: EvalScenario) -> bool:
    """Return whether answer satisfies contains and any_of assertion groups."""
    answer_lower = answer.lower()
    if scenario.expected_answer_contains:
        required = [item.lower() for item in scenario.expected_answer_contains]
        if not all(item in answer_lower for item in required):
            return False
    for group in scenario.expected_answer_any_of:
        options = [item.lower() for item in group if item]
        if options and not any(item in answer_lower for item in options):
            return False
    return True


_FORBIDDEN_PYTHON_IMPORTS = ("numpy", "scipy", "pandas", "sklearn", "sympy")


def _python_codes_from_tool_results(tool_results: list[Any]) -> list[str]:
    codes: list[str] = []
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        call = item.get("call")
        if not isinstance(call, dict) or call.get("tool_name") != "python":
            continue
        args = call.get("args")
        if isinstance(args, dict):
            codes.append(str(args.get("code") or ""))
    return codes


def _forbidden_imports_in_code(code: str) -> list[str]:
    hits: list[str] = []
    for name in _FORBIDDEN_PYTHON_IMPORTS:
        if re.search(rf"\b(?:import|from)\s+{re.escape(name)}\b", code):
            hits.append(name)
    return hits


def _forbidden_python_imports_after_first_python(tool_results: list[Any]) -> list[str]:
    """Flag third-party imports only in python calls after the first (post-policy retry)."""
    codes = _python_codes_from_tool_results(tool_results)
    if len(codes) <= 1:
        return []
    hits: list[str] = []
    seen: set[str] = set()
    for code in codes[1:]:
        for name in _forbidden_imports_in_code(code):
            if name not in seen:
                seen.add(name)
                hits.append(name)
    return hits


def classify_bug_tags(
    *,
    status: str,
    terminal_reason: str | None,
    expected_tools_missing: list[str],
    forbidden_tools_used: list[str],
    empty_tool_results: int,
    repeated_tools: list[str],
    forbidden_python_imports: list[str] | None = None,
) -> list[str]:
    """Classify likely issue categories for triage."""
    tags: list[str] = []
    if status == "failed":
        tags.append("runtime_loop_or_limits")
    if terminal_reason == "model_error":
        tags.append("provider_protocol")
    if expected_tools_missing:
        tags.append("prompt_or_tool_selection")
    if forbidden_tools_used:
        tags.append("tool_governance")
    if empty_tool_results > 0:
        tags.append("tool_implementation")
    if repeated_tools:
        tags.append("efficiency")
    if forbidden_python_imports:
        tags.append("python_forbidden_import")
    if not tags:
        tags.append("none")
    return tags


def _detect_answer_language(answer: str) -> str:
    if not answer.strip():
        return "unknown"
    cyrillic = sum(1 for ch in answer if "а" <= ch.lower() <= "я")
    latin = sum(1 for ch in answer if "a" <= ch.lower() <= "z")
    if cyrillic > latin:
        return "ru"
    if latin > 0:
        return "en"
    return "unknown"


def _is_subsequence(*, expected: list[str], actual: list[str]) -> bool:
    if not expected:
        return True
    index = 0
    for item in actual:
        if item == expected[index]:
            index += 1
            if index >= len(expected):
                return True
    return False
