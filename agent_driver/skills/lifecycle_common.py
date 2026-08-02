"""Small shared skill-lifecycle helpers (product-family mappings).

Base layer with no ``agent_driver`` imports beyond contracts, imported by both
``lifecycle`` and ``lifecycle_evidence`` so neither has to depend on the other
for these leaf mappings (keeps the split a DAG).
"""

from __future__ import annotations


def _primary_skill_scenario(product_family: str) -> str:
    if product_family == "excel_ai":
        return "skills_lifecycle.excel_workbook_skills.v1"
    if product_family == "chat_demo":
        return "skills_lifecycle.chat_demo_research_skills.v1"
    return "skills_lifecycle.selection_evidence.v1"


def _pack_id_for_product(product_family: str) -> str | None:
    if product_family == "excel_ai":
        return "excel_workbook_chat"
    if product_family == "chat_demo":
        return "deep_research_chat_demo"
    return None


__all__ = ["_primary_skill_scenario", "_pack_id_for_product"]
