"""Small chat task contracts inspired by Hermes' specify step.

The contract is intentionally deterministic and cheap: it gives the model a
compact shape for ambiguous chat work without adding another orchestration
layer or LLM preflight.
"""

from __future__ import annotations

from typing import Any

from agent_driver.runtime.research_evidence import (
    RESEARCH_DEPTH_DEEP_PARALLEL,
    RESEARCH_DEPTH_NONE,
    RESEARCH_DEPTH_SOURCE_VERIFIED,
    classify_research_depth,
)

_DELIVERABLE_MARKERS = (
    "не план",
    "напиши",
    "черновик",
    "реферат",
    "финальный ответ",
    "итоговый ответ",
    "write",
    "draft",
    "final answer",
    "not a plan",
)

_RESEARCH_MARKERS = (
    "найди",
    "поиск",
    "интернет",
    "источник",
    "исслед",
    "research",
    "search",
    "source",
)

_FETCH_REQUIRED_MARKERS = (
    "открой",
    "открыть",
    "загрузи",
    "прочитай url",
    "web_fetch",
    "fetch",
    "open url",
    "open the url",
)

_NO_RESEARCH_MARKERS = (
    "без поиска",
    "без интернета",
    "не ищи",
    "не используй интернет",
    "по памяти",
    "no search",
    "without search",
    "without web",
    "do not search",
)

_IMPLEMENTATION_MARKERS = (
    "реализ",
    "исправ",
    "добав",
    "почини",
    "implement",
    "fix",
    "add",
    "change",
)

_PLAN_ONLY_MARKERS = (
    "только план",
    "только план работ",
    "только план поиска",
    "без реферата",
    "без черновика",
    "plan only",
    "only plan",
    "just the plan",
    "no report",
    "without writing",
)


def build_chat_task_contract(message: str) -> dict[str, Any] | None:
    """Return a compact task contract for complex chat turns.

    This mirrors Hermes' ``kanban_specify`` idea, but keeps the first pass in
    normal Python: classify obvious intent, provide a tiny goal/approach/
    acceptance/out-of-scope block, and let the main model solve the task.
    """
    text = " ".join(message.strip().split())
    if not text:
        return None
    lowered = text.lower()
    if _is_plan_only_request(lowered):
        return {
            "kind": "plan",
            "requires_research": False,
            "research_depth": RESEARCH_DEPTH_NONE,
            "goal": text,
            "approach": (
                "Create a concise checklist/plan for the requested work without "
                "executing research or writing the deliverable."
            ),
            "acceptance_criteria": [
                "Uses the visible checklist when a multi-step plan is useful.",
                "Does not perform web/data research unless the user asks to execute.",
                "Does not write the deliverable that was explicitly excluded.",
            ],
            "out_of_scope": [
                "Starting the research/search phase.",
                "Writing the report/draft instead of the plan.",
            ],
        }
    if any(marker in lowered for marker in _DELIVERABLE_MARKERS):
        requires_research = _requires_research(lowered)
        fetch_required = _requires_fetch(lowered)
        research_depth = classify_research_depth(
            lowered,
            requires_research=requires_research,
        )
        criteria = [
            "Final response contains the requested deliverable, not another plan.",
            "Reasonable assumptions are stated briefly when details are missing.",
            (
                "Clarifying questions are used only for truly blocking "
                "user-owned decisions."
            ),
        ]
        if requires_research:
            criteria.insert(
                1,
                (
                    "Because the goal asks for internet/search/source work, "
                    "use available web/data tools before the final answer."
                ),
            )
        return {
            "kind": "deliverable",
            "requires_research": requires_research,
            "research_depth": research_depth,
            "fetch_required": fetch_required,
            "goal": text,
            "approach": (
                "Use existing context plus only the tools needed for missing "
                "facts, then deliver the requested answer in this turn."
            ),
            "acceptance_criteria": criteria,
            "out_of_scope": [
                "Restarting the plan/checklist instead of answering.",
                "Asking for plan approval for research or writing deliverables.",
            ],
        }
    if _requires_research(lowered):
        research_depth = classify_research_depth(lowered, requires_research=True)
        fetch_required = _requires_fetch(lowered)
        return {
            "kind": "research",
            "requires_research": True,
            "research_depth": research_depth,
            "fetch_required": fetch_required,
            "goal": text,
            "approach": (
                "Gather evidence with data tools, verify enough context, then "
                "summarize concrete findings."
            ),
            "acceptance_criteria": [
                "Uses data tools when external/current facts are needed.",
                (
                    "Final response cites or names concrete sources when web "
                    "data was used."
                ),
                "Stops once enough evidence exists instead of looping searches.",
            ],
            "out_of_scope": [
                "Modal plan approval for pure research.",
                "Progress narration without a synthesized answer.",
            ],
        }
    if any(marker in lowered for marker in _IMPLEMENTATION_MARKERS):
        return {
            "kind": "implementation",
            "goal": text,
            "approach": (
                "Inspect relevant context first; use approval planning for "
                "non-trivial or risky side effects."
            ),
            "acceptance_criteria": [
                "Plan before multi-file, risky, or preference-dependent changes.",
                "Execute approved work with tools rather than describing future work.",
                "Report changed artifacts and checks run.",
            ],
            "out_of_scope": [
                "Using approval planning for trivial single-step edits.",
                "Making side-effecting changes without required approval.",
            ],
        }
    return None


def render_task_contract_reminder(contract: dict[str, Any]) -> str | None:
    """Render a compact model-facing reminder from a task contract."""
    kind = str(contract.get("kind") or "").strip()
    goal = str(contract.get("goal") or "").strip()
    approach = str(contract.get("approach") or "").strip()
    criteria = _string_list(contract.get("acceptance_criteria"))
    out_of_scope = _string_list(contract.get("out_of_scope"))
    if not kind or not goal:
        return None
    parts = [
        f"Runtime reminder: task_contract_active ({kind}).",
        f"Goal: {goal}",
    ]
    if approach:
        parts.append(f"Approach: {approach}")
    if contract.get("requires_research") is True:
        parts.append(
            "Research requirement: user explicitly asked for internet/search/source "
            "work; use available web/data tools before the final answer."
        )
    if contract.get("fetch_required") is True:
        parts.append(
            "Fetch requirement: the user explicitly asked to open/read a URL; "
            "use web_fetch before the final answer when it is available."
        )
    if contract.get("research_depth") in {
        RESEARCH_DEPTH_SOURCE_VERIFIED,
        RESEARCH_DEPTH_DEEP_PARALLEL,
    }:
        depth = contract.get("research_depth")
        parts.append(
            f"Research depth: {depth}. Treat search results as "
            "candidates; fetch/open concrete URLs before final synthesis when "
            "web_fetch is available."
        )
    if criteria:
        parts.append("Acceptance criteria: " + "; ".join(criteria[:4]))
    if out_of_scope:
        parts.append("Out of scope: " + "; ".join(out_of_scope[:3]))
    return " ".join(parts)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _is_plan_only_request(text: str) -> bool:
    return any(marker in text for marker in _PLAN_ONLY_MARKERS)


def _requires_research(text: str) -> bool:
    if any(marker in text for marker in _NO_RESEARCH_MARKERS):
        return False
    return any(marker in text for marker in _RESEARCH_MARKERS)


def _requires_fetch(text: str) -> bool:
    return any(marker in text for marker in _FETCH_REQUIRED_MARKERS)


__all__ = ["build_chat_task_contract", "render_task_contract_reminder"]
