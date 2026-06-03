"""Research analyzers for run trace summaries."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from agent_driver.observability.run_trace.planning import (
    is_plan_only_prompt,
    planning_todos_incomplete,
)
from agent_driver.observability.run_trace.tools import tool_payloads
from agent_driver.runtime.research_evidence import (
    RESEARCH_DEPTH_SOURCE_VERIFIED,
    SOURCE_VERIFIED_DOMAINS,
    SOURCE_VERIFIED_FETCHES,
    classify_research_depth,
)

READ_SOURCE_TOOLS = frozenset({"web_fetch", "source_read", "pdf_read", "browser_read"})
RESEARCH_TOOLS = frozenset({"web_search", *READ_SOURCE_TOOLS})
FETCH_REQUIRED_MARKERS = (
    "открой",
    "открыть",
    "загрузи",
    "прочитай url",
    "web_fetch",
    "fetch",
    "open url",
    "open the url",
)


def requires_research(
    *,
    task_contract: dict[str, Any] | None,
    user_prompt: str | None,
) -> bool:
    text = " ".join((user_prompt or "").lower().split())
    if any(
        marker in text
        for marker in (
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
    ):
        return False
    if (
        isinstance(task_contract, dict)
        and task_contract.get("requires_research") is True
    ):
        return True
    if is_plan_only_prompt(text):
        return False
    return any(
        marker in text
        for marker in (
            "найди",
            "поиск",
            "интернет",
            "источник",
            "research",
            "search",
            "source",
        )
    )


def research_summary(
    events: list[dict[str, object]],
    *,
    tool_names: list[str],
    requires_research: bool,
    user_prompt: str | None,
    assistant_text: str,
    task_contract: dict[str, Any] | None,
    planning: dict[str, Any],
) -> dict[str, Any]:
    depth = research_depth(
        task_contract=task_contract,
        user_prompt=user_prompt,
        requires_research=requires_research,
    )
    search_count = tool_names.count("web_search")
    all_fetch_payloads = [
        payload
        for tool_name in READ_SOURCE_TOOLS
        for payload in tool_payloads(events, tool_name)
    ]
    fetch_payloads = [
        payload for payload in all_fetch_payloads if tool_payload_succeeded(payload)
    ]
    failed_fetch_payloads = [
        payload for payload in all_fetch_payloads if not tool_payload_succeeded(payload)
    ]
    research_payloads = [
        payload
        for tool_name in RESEARCH_TOOLS
        for payload in tool_payloads(events, tool_name)
        if tool_payload_succeeded(payload)
    ]
    fetch_count = len(fetch_payloads)
    failed_fetch_count = len(failed_fetch_payloads)
    fetch_attempt_count = len(all_fetch_payloads)
    domains = unique_domains(fetch_payloads)
    final_has_source_links = has_source_links(assistant_text) or has_tool_sources(
        research_payloads
    )
    fetch_required = is_fetch_required(
        task_contract=task_contract,
        user_prompt=user_prompt,
    )
    fetch_fallback_required = (
        depth == RESEARCH_DEPTH_SOURCE_VERIFIED
        and failed_fetch_count >= SOURCE_VERIFIED_FETCHES
        and fetch_count == 0
        and (search_count > 0 or fetch_attempt_count > 0)
    )
    fetch_required_but_missing = (
        (depth == RESEARCH_DEPTH_SOURCE_VERIFIED or fetch_required)
        and search_count > 0
        and fetch_count < (SOURCE_VERIFIED_FETCHES if not fetch_required else 1)
        and not fetch_fallback_required
    )
    insufficient_source_diversity = (
        depth == RESEARCH_DEPTH_SOURCE_VERIFIED
        and fetch_count >= SOURCE_VERIFIED_FETCHES
        and len(domains) < SOURCE_VERIFIED_DOMAINS
    )
    final_missing_source_links = (
        depth == RESEARCH_DEPTH_SOURCE_VERIFIED
        and fetch_count >= SOURCE_VERIFIED_FETCHES
        and len(domains) >= SOURCE_VERIFIED_DOMAINS
        and not final_has_source_links
    )
    repair_reasons: list[str] = []
    if planning_todos_incomplete(
        planning,
        assistant_text=assistant_text,
        allow_all_todos=research_final_metrics_cover_plan_todos(
            requires_research=requires_research,
            search_count=search_count,
            fetch_required_but_missing=fetch_required_but_missing,
            insufficient_source_diversity=insufficient_source_diversity,
            final_missing_source_links=final_missing_source_links,
            assistant_text=assistant_text,
        ),
    ):
        repair_reasons.append("unfinished_todos")
    if requires_research and search_count == 0 and fetch_count == 0:
        repair_reasons.append("missing_research_evidence")
    if fetch_required_but_missing:
        repair_reasons.append("missing_fetched_sources")
    if insufficient_source_diversity:
        repair_reasons.append("insufficient_source_diversity")
    if final_missing_source_links:
        repair_reasons.append("final_missing_source_links")
    final_readiness = "repair_needed" if repair_reasons else "allowed"
    return {
        "depth": depth,
        "search_count": search_count,
        "fetch_count": fetch_count,
        "fetch_attempt_count": fetch_attempt_count,
        "failed_fetch_count": failed_fetch_count,
        "fetch_fallback_required": fetch_fallback_required,
        "required_fetch_count": (
            SOURCE_VERIFIED_FETCHES
            if depth == RESEARCH_DEPTH_SOURCE_VERIFIED
            else (1 if requires_research or fetch_required else 0)
        ),
        "fetch_required": fetch_required,
        "fetch_required_but_missing": fetch_required_but_missing,
        "insufficient_source_diversity": insufficient_source_diversity,
        "unique_domains": domains,
        "final_has_source_links": final_has_source_links,
        "final_missing_source_links": final_missing_source_links,
        "final_readiness": final_readiness,
        "repair_required_reasons": repair_reasons,
    }


def research_depth(
    *,
    task_contract: dict[str, Any] | None,
    user_prompt: str | None,
    requires_research: bool,
) -> str:
    if isinstance(task_contract, dict):
        depth = task_contract.get("research_depth")
        if isinstance(depth, str) and depth:
            return depth
    return classify_research_depth(
        user_prompt or "",
        requires_research=requires_research,
        plan_only=is_plan_only_prompt(" ".join((user_prompt or "").lower().split())),
    )


def is_fetch_required(
    *,
    task_contract: dict[str, Any] | None,
    user_prompt: str | None,
) -> bool:
    if isinstance(task_contract, dict) and task_contract.get("fetch_required") is True:
        return True
    text = " ".join((user_prompt or "").lower().split())
    return any(marker in text for marker in FETCH_REQUIRED_MARKERS)


def unique_domains(payloads: list[dict[str, Any]]) -> list[str]:
    domains: list[str] = []
    for payload in payloads:
        url = payload_url(payload)
        if not url:
            continue
        domain = urlparse(url).netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        if domain and domain not in domains:
            domains.append(domain)
    return domains


def payload_url(payload: dict[str, Any]) -> str | None:
    args = payload.get("args")
    if isinstance(args, dict):
        url = args.get("url")
        if isinstance(url, str) and url:
            return url
    url = payload.get("url")
    if isinstance(url, str) and url:
        return url
    return None


def tool_payload_succeeded(payload: dict[str, Any]) -> bool:
    status = str(payload.get("status") or "").lower()
    if status in {"failed", "error", "denied", "timed_out", "timeout"}:
        return False
    status_code = payload.get("status_code")
    if isinstance(status_code, int) and not isinstance(status_code, bool):
        return status_code < 400
    if payload.get("error_code") or payload.get("error"):
        return False
    summary = str(payload.get("result_summary") or "").lower()
    if re.search(r"\bhttp\s+[45]\d\d\b", summary):
        return False
    if any(
        marker in summary
        for marker in ("failed:", "blocked by upstream", "unsupported content type")
    ):
        return False
    return True


def has_tool_sources(payloads: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(payload.get("sources"), list) and payload["sources"]
        for payload in payloads
    )


def has_source_links(text: str) -> bool:
    return bool(re.search(r"https?://|\[[^\]]+\]\(https?://", text))


def research_final_answer_covers_plan_todos(
    *,
    requires_research: bool,
    research: dict[str, Any],
    assistant_text: str,
) -> bool:
    return research_final_metrics_cover_plan_todos(
        requires_research=requires_research,
        search_count=int(research.get("search_count") or 0),
        fetch_required_but_missing=bool(research.get("fetch_required_but_missing")),
        insufficient_source_diversity=bool(
            research.get("insufficient_source_diversity")
        ),
        final_missing_source_links=bool(research.get("final_missing_source_links")),
        assistant_text=assistant_text,
    )


def research_final_metrics_cover_plan_todos(
    *,
    requires_research: bool,
    search_count: int,
    fetch_required_but_missing: bool,
    insufficient_source_diversity: bool,
    final_missing_source_links: bool,
    assistant_text: str,
) -> bool:
    if not requires_research:
        return False
    if search_count <= 0:
        return False
    if len(assistant_text.strip()) < 200:
        return False
    return not (
        fetch_required_but_missing
        or insufficient_source_diversity
        or final_missing_source_links
    )


__all__ = [
    "RESEARCH_TOOLS",
    "requires_research",
    "research_final_answer_covers_plan_todos",
    "research_summary",
]
