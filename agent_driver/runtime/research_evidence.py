"""Small helpers for chat research depth and evidence accounting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

RESEARCH_DEPTH_NONE = "none"
RESEARCH_DEPTH_LIGHT = "light_search"
RESEARCH_DEPTH_SOURCE_VERIFIED = "source_verified_report"
WEB_SEARCH_TOOL = "web_search"
WEB_FETCH_TOOL = "web_fetch"
SOURCE_VERIFIED_FETCHES = 2
SOURCE_VERIFIED_DOMAINS = 2

_SOURCE_VERIFIED_MARKERS = (
    "deep research",
    "literature review",
    "report",
    "sources",
    "обзор",
    "отчет",
    "отчёт",
    "реферат",
    "исслед",
    "поискать информацию",
    "найти информацию",
    "найди информацию",
    "информацию",
    "составь todo",
    "составь туду",
    "иди по нему",
    "сравни",
    "сравнение",
)

_LIGHT_RESEARCH_MARKERS = (
    "один источник",
    "одну ссылку",
    "свежую ссылку",
    "найди ссылку",
    "find a source",
    "one source",
    "one link",
)


@dataclass(frozen=True)
class ResearchEvidenceState:
    """Evidence counters derived from completed tool results."""

    search_calls: int = 0
    fetch_calls: int = 0
    successful_fetches: int = 0
    failed_fetches: int = 0
    unique_domains: tuple[str, ...] = ()

    def source_verified(
        self,
        *,
        required_fetches: int = SOURCE_VERIFIED_FETCHES,
        required_domains: int = SOURCE_VERIFIED_DOMAINS,
    ) -> bool:
        """Return True when enough fetched sources exist for report-like work."""
        return (
            self.successful_fetches >= required_fetches
            and len(self.unique_domains) >= required_domains
        )


def classify_research_depth(
    text: str,
    *,
    requires_research: bool,
    plan_only: bool = False,
) -> str:
    """Classify how much evidence a chat research request needs."""
    if plan_only or not requires_research:
        return RESEARCH_DEPTH_NONE
    normalized = " ".join(text.lower().split())
    if any(marker in normalized for marker in _LIGHT_RESEARCH_MARKERS):
        return RESEARCH_DEPTH_LIGHT
    if any(marker in normalized for marker in _SOURCE_VERIFIED_MARKERS):
        return RESEARCH_DEPTH_SOURCE_VERIFIED
    return RESEARCH_DEPTH_LIGHT


def research_evidence_from_tool_results(
    tool_results: object,
) -> ResearchEvidenceState:
    """Count web_search/web_fetch evidence from normalized tool result rows."""
    if not isinstance(tool_results, list):
        return ResearchEvidenceState()
    search_calls = 0
    fetch_calls = 0
    successful_fetches = 0
    failed_fetches = 0
    domains: list[str] = []
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        call = item.get("call")
        if not isinstance(call, dict):
            continue
        tool_name = str(call.get("tool_name") or "").strip()
        if tool_name == WEB_SEARCH_TOOL:
            search_calls += 1
        elif tool_name == WEB_FETCH_TOOL:
            fetch_calls += 1
            if _tool_result_failed(item):
                failed_fetches += 1
            else:
                successful_fetches += 1
                _append_domain(domains, _tool_result_url(item))
    return ResearchEvidenceState(
        search_calls=search_calls,
        fetch_calls=fetch_calls,
        successful_fetches=successful_fetches,
        failed_fetches=failed_fetches,
        unique_domains=tuple(domains),
    )


def _tool_result_failed(item: dict[str, Any]) -> bool:
    if item.get("error"):
        return True
    decision = str(item.get("decision") or "").lower()
    if decision == "deny":
        return True
    structured = item.get("structured_output")
    if isinstance(structured, dict):
        if structured.get("error") or structured.get("error_code"):
            return True
        status = str(structured.get("status") or "").lower()
        if status in {"error", "failed", "denied"}:
            return True
    return False


def _tool_result_url(item: dict[str, Any]) -> str | None:
    structured = item.get("structured_output")
    if isinstance(structured, dict):
        url = structured.get("url")
        if isinstance(url, str) and url:
            return url
    call = item.get("call")
    if isinstance(call, dict):
        args = call.get("args")
        if isinstance(args, dict):
            url = args.get("url")
            if isinstance(url, str) and url:
                return url
    return None


def _append_domain(domains: list[str], url: str | None) -> None:
    if not url:
        return
    domain = urlparse(url).netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    if domain and domain not in domains:
        domains.append(domain)


__all__ = [
    "RESEARCH_DEPTH_LIGHT",
    "RESEARCH_DEPTH_NONE",
    "RESEARCH_DEPTH_SOURCE_VERIFIED",
    "SOURCE_VERIFIED_DOMAINS",
    "SOURCE_VERIFIED_FETCHES",
    "ResearchEvidenceState",
    "classify_research_depth",
    "research_evidence_from_tool_results",
]
