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

_WEB_TOOL_PRESETS = frozenset({"web_search", "web", "safe", "workspace", "dev", "all"})


def is_deliverable_request(message: str) -> bool:
    """Return true when the user asks for the final artifact, not another plan."""
    text = " ".join(message.lower().split())
    return any(marker in text for marker in _DELIVERABLE_MARKERS)


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
    """Force a first web search when the user explicitly asked for research."""
    task_contract = policy.metadata.get("task_contract")
    if not isinstance(task_contract, dict):
        return None
    if task_contract.get("requires_research") is not True:
        return None
    if preset in _WEB_TOOL_PRESETS:
        return {"type": "tool", "name": "web_search"}
    return None


__all__ = [
    "build_chat_tool_policy",
    "initial_tool_choice_for_chat",
    "is_deliverable_request",
]
