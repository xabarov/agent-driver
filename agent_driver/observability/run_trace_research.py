"""Compatibility shim for run-trace research analyzers."""

from agent_driver.observability.run_trace.research import (
    RESEARCH_TOOLS,
    has_source_links,
    has_tool_sources,
    is_fetch_required,
    payload_url,
    requires_research,
    research_depth,
    research_final_answer_covers_plan_todos,
    research_final_metrics_cover_plan_todos,
    research_summary,
    tool_payload_succeeded,
    unique_domains,
)

__all__ = [
    "RESEARCH_TOOLS",
    "has_source_links",
    "has_tool_sources",
    "is_fetch_required",
    "payload_url",
    "requires_research",
    "research_depth",
    "research_final_answer_covers_plan_todos",
    "research_final_metrics_cover_plan_todos",
    "research_summary",
    "tool_payload_succeeded",
    "unique_domains",
]
