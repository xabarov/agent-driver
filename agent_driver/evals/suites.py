"""General (non-coding) task suite for low-budget harness comparison (T0).

Coding agents are well-served by dedicated benchmarks; this suite targets the
*general* capabilities our runtime mechanics exercise — tool-use, multi-turn
dialog, retrieval-lite, summarization, planning, and cross-turn memory recall.
Each item is provider-agnostic data (prompt + a ``category`` tag); the caller
wires the toolset/provider. Deterministic and dependency-free.
"""

from __future__ import annotations

from agent_driver.batch.contracts import BatchItem

# (category, prompt) pairs. Categories map to the mechanics each task stresses.
_GENERAL_TASKS: tuple[tuple[str, str], ...] = (
    ("tool_use", "Search for the current population of Tokyo and state the number."),
    ("tool_use", "Fetch https://example.com and report the page's title."),
    ("tool_use", "List the files in the current workspace, then read the first one."),
    ("dialog", "I'm planning a 3-day trip to Lisbon. Ask me one clarifying question."),
    ("dialog", "Explain the difference between TCP and UDP to a non-engineer."),
    (
        "retrieval",
        "Given these notes, what is the deploy target? Notes: region=eu-west-3.",
    ),
    (
        "retrieval",
        "From this text, extract every email address: contact a@x.io or b@y.io.",
    ),
    (
        "summarization",
        "Summarize in one sentence: a long meeting about Q3 budget cuts.",
    ),
    (
        "summarization",
        "Condense these 5 bullet points into a 2-line executive summary.",
    ),
    ("planning", "Break 'launch a newsletter' into an ordered todo list of <=5 steps."),
    ("planning", "Plan how to migrate a small SQLite DB to Postgres; list the steps."),
    ("memory", "Remember that my preferred language is Python, then acknowledge."),
)


def general_task_suite(*, prefix: str = "gen") -> list[BatchItem]:
    """Return the general non-coding task suite as :class:`BatchItem`s.

    Each item's ``metadata["category"]`` tags the capability it stresses, so a
    report can break results down by category.
    """
    return [
        BatchItem(
            item_id=f"{prefix}_{index:02d}_{category}",
            input=prompt,
            metadata={"category": category},
        )
        for index, (category, prompt) in enumerate(_GENERAL_TASKS)
    ]


def suite_categories() -> tuple[str, ...]:
    """Distinct categories present in the general suite (sorted)."""
    return tuple(sorted({category for category, _ in _GENERAL_TASKS}))


__all__ = ["general_task_suite", "suite_categories"]
