"""Reusable chat-facing tool policy helpers."""

from __future__ import annotations

from agent_driver.contracts.tools import ToolPolicyInput
from agent_driver.runtime.planning_policy import classify_planning_hint
from agent_driver.runtime.task_contract import build_chat_task_contract

_DELIVERABLE_MARKERS = (
    "не план",
    "напиши реферат",
    "напиши черновик",
    "связный черновик",
    "финальный ответ",
    "итоговый ответ",
    "write the report",
    "write a report",
    "draft the report",
    "draft an essay",
    "final answer",
    "not a plan",
)

_PYTHON_RELIABILITY_MARKERS = (
    "сколько",
    "посчитай",
    "вычисли",
    "рассчитай",
    "проверь точно",
    "точно",
    "процент",
    "среднее",
    "медиан",
    "вероятност",
    "комбинац",
    "combination",
    "combinatorics",
    "probability",
    "calculate",
    "compute",
    "exact",
    "count",
    "average",
    "median",
)
_PYTHON_RELIABILITY_SYMBOLS = frozenset("0123456789*/%=+")


def is_deliverable_request(message: str) -> bool:
    """Return true when the user asks for the final artifact, not another plan."""
    text = " ".join(message.lower().split())
    return any(marker in text for marker in _DELIVERABLE_MARKERS)


def is_python_reliability_request(message: str) -> bool:
    """Return true when exact calculation/counting should use python."""
    text = " ".join(message.lower().split())
    if not text:
        return False
    if any(marker in text for marker in _PYTHON_RELIABILITY_MARKERS):
        return True
    return any(symbol in text for symbol in _PYTHON_RELIABILITY_SYMBOLS)


def build_chat_tool_policy(
    message: str,
    *,
    force_planning: bool = False,
    force_planning_mode: str = "auto",
) -> ToolPolicyInput:
    """Build a conservative default tool policy for chat-style hosts."""
    metadata: dict[str, object] = {}
    hint = classify_planning_hint(message)
    metadata["planning_hint"] = hint.model_dump(mode="json")
    task_contract = build_chat_task_contract(message)
    if task_contract is not None:
        metadata["task_contract"] = task_contract
    if is_python_reliability_request(message):
        metadata["python_reliability_request"] = {
            "enabled": True,
            "reason": "exact calculation/counting is more reliable through python",
        }
    contract_kind = (
        str(task_contract.get("kind"))
        if isinstance(task_contract, dict) and task_contract.get("kind")
        else ""
    )
    denied_tools: list[str] | None = None
    if is_deliverable_request(message) or contract_kind == "deliverable":
        metadata["deliverable_request"] = {
            "enabled": True,
            "reason": "user asked to produce the deliverable now",
        }
        denied_tools = [
            "ask_user_question",
            "enter_plan_mode",
            "exit_plan_mode_v2",
        ]
    elif contract_kind == "plan":
        metadata["plan_only_request"] = {
            "enabled": True,
            "reason": "user asked for a plan without executing the work",
        }
        denied_tools = ["web_search", "web_fetch"]
    elif contract_kind == "research":
        metadata["research_request"] = {
            "enabled": True,
            "reason": "user gave a research target; proceed with available sources",
        }
        denied_tools = ["ask_user_question"]
    if force_planning:
        metadata["force_planning"] = {
            "enabled": True,
            "mode": force_planning_mode,
        }
    return ToolPolicyInput(metadata=metadata, denied_tools=denied_tools)


def initial_tool_choice_for_chat(
    *,
    policy: ToolPolicyInput,
    preset: str,
) -> str | dict[str, object] | None:
    """Return an optional initial tool choice for chat runs.

    Research is steered through task contracts and prompt fragments instead of
    a forced provider-level tool_choice. Some OpenRouter routes reject named
    forced tool choices even when normal auto tool use is supported.
    """
    _ = policy, preset
    return None


__all__ = [
    "build_chat_tool_policy",
    "initial_tool_choice_for_chat",
    "is_deliverable_request",
    "is_python_reliability_request",
]
