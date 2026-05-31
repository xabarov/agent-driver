"""Small helpers for chat research depth and evidence accounting."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

RESEARCH_DEPTH_NONE = "none"
RESEARCH_DEPTH_LIGHT = "light_search"
RESEARCH_DEPTH_SOURCE_VERIFIED = "source_verified_report"
RESEARCH_DEPTH_DEEP_PARALLEL = "deep_parallel_research"
WEB_SEARCH_TOOL = "web_search"
WEB_FETCH_TOOL = "web_fetch"
SOURCE_VERIFIED_FETCHES = 2
SOURCE_VERIFIED_DOMAINS = 2

_ASSISTANT_URL_RE = re.compile(r"https?://[^\s\]\)>,]+")

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


@dataclass(frozen=True)
class ResearchSourceLedger:
    """First-class source ledger for research runs.

    Search hits are candidates. Only verified reads should satisfy final
    report readiness.
    """

    search_candidates: list[dict[str, Any]] = field(default_factory=list)
    verified_reads: list[dict[str, Any]] = field(default_factory=list)
    failed_reads: list[dict[str, Any]] = field(default_factory=list)
    blocked_reads: list[dict[str, Any]] = field(default_factory=list)
    assistant_links: list[dict[str, Any]] = field(default_factory=list)

    def model_dump(self) -> dict[str, Any]:
        """Return JSON-compatible ledger payload."""
        return {
            "search_candidates": self.search_candidates,
            "verified_reads": self.verified_reads,
            "failed_reads": self.failed_reads,
            "blocked_reads": self.blocked_reads,
            "assistant_links": self.assistant_links,
        }


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
    if "deep parallel research" in normalized or "deep_parallel_research" in normalized:
        return RESEARCH_DEPTH_DEEP_PARALLEL
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


def research_source_ledger_from_tool_results(
    tool_results: object, *, assistant_text: str = ""
) -> ResearchSourceLedger:
    """Build a compact source ledger from tool rows plus assistant-visible links."""
    if not isinstance(tool_results, list):
        return ResearchSourceLedger(assistant_links=_assistant_links(assistant_text))
    search_candidates: list[dict[str, Any]] = []
    verified_reads: list[dict[str, Any]] = []
    failed_reads: list[dict[str, Any]] = []
    blocked_reads: list[dict[str, Any]] = []
    seen_candidates: set[str] = set()
    seen_verified: set[str] = set()
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        call = item.get("call")
        if not isinstance(call, dict):
            continue
        tool_name = str(call.get("tool_name") or "").strip()
        tool_call_id = _clean_str(call.get("tool_call_id"))
        if tool_name == WEB_SEARCH_TOOL:
            for candidate in _search_candidates(item, tool_call_id=tool_call_id):
                _append_unique_source(
                    search_candidates, candidate, seen=seen_candidates
                )
        elif tool_name == WEB_FETCH_TOOL:
            record = _fetch_record(item, tool_call_id=tool_call_id)
            if record is None:
                continue
            if _tool_result_blocked(item):
                blocked_reads.append(record)
            elif _tool_result_failed(item):
                failed_reads.append(record)
            else:
                _append_unique_source(verified_reads, record, seen=seen_verified)
    return ResearchSourceLedger(
        search_candidates=search_candidates,
        verified_reads=verified_reads,
        failed_reads=failed_reads,
        blocked_reads=blocked_reads,
        assistant_links=_assistant_links(assistant_text),
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


def _tool_result_blocked(item: dict[str, Any]) -> bool:
    decision = str(item.get("decision") or "").lower()
    if decision in {"deny", "interrupt"}:
        return True
    structured = item.get("structured_output")
    if isinstance(structured, dict):
        status = str(structured.get("status") or "").lower()
        return status == "denied"
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


def _search_candidates(
    item: dict[str, Any], *, tool_call_id: str | None
) -> list[dict[str, Any]]:
    structured = item.get("structured_output")
    if not isinstance(structured, dict):
        return []
    results = structured.get("results")
    if not isinstance(results, list):
        return []
    rows: list[dict[str, Any]] = []
    for index, result in enumerate(results, start=1):
        if not isinstance(result, dict):
            continue
        url = _clean_str(result.get("url"))
        if not url:
            continue
        row = {
            "url": url,
            "domain": _domain(url),
            "rank": index,
            "source_type": WEB_SEARCH_TOOL,
        }
        _set_clean(row, "title", result.get("title"))
        _set_clean(row, "excerpt", result.get("snippet"))
        if tool_call_id:
            row["tool_call_id"] = tool_call_id
        rows.append(row)
    return rows


def _fetch_record(
    item: dict[str, Any], *, tool_call_id: str | None
) -> dict[str, Any] | None:
    url = _tool_result_url(item)
    if not url:
        return None
    structured = item.get("structured_output")
    row: dict[str, Any] = {
        "url": url,
        "domain": _domain(url),
        "source_type": WEB_FETCH_TOOL,
    }
    if tool_call_id:
        row["tool_call_id"] = tool_call_id
    if isinstance(structured, dict):
        _set_clean(row, "title", _metadata_value(structured.get("metadata"), "title"))
        _set_clean(
            row,
            "excerpt",
            structured.get("excerpt") or structured.get("summary"),
        )
        _set_clean(row, "error_code", structured.get("error_code"))
        _set_clean(row, "status", structured.get("status"))
        status_code = structured.get("status_code")
        if isinstance(status_code, int) and not isinstance(status_code, bool):
            row["status_code"] = status_code
    return row


def _assistant_links(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in _ASSISTANT_URL_RE.finditer(text or ""):
        url = match.group(0).rstrip(".,;:")
        if url in seen:
            continue
        seen.add(url)
        rows.append(
            {"url": url, "domain": _domain(url), "source_type": "assistant_link"}
        )
    return rows


def _append_unique_source(
    rows: list[dict[str, Any]], row: dict[str, Any], *, seen: set[str]
) -> None:
    url = str(row.get("url") or "")
    key = url.lower()
    if not key or key in seen:
        return
    seen.add(key)
    rows.append(row)


def _metadata_value(metadata: object, key: str) -> object:
    return metadata.get(key) if isinstance(metadata, dict) else None


def _set_clean(row: dict[str, Any], key: str, value: object) -> None:
    cleaned = _clean_str(value)
    if cleaned:
        row[key] = cleaned[:500]


def _clean_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.strip().split())
    return cleaned or None


def _domain(url: str) -> str | None:
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain or None


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
    "RESEARCH_DEPTH_DEEP_PARALLEL",
    "RESEARCH_DEPTH_NONE",
    "RESEARCH_DEPTH_SOURCE_VERIFIED",
    "SOURCE_VERIFIED_DOMAINS",
    "SOURCE_VERIFIED_FETCHES",
    "ResearchEvidenceState",
    "ResearchSourceLedger",
    "classify_research_depth",
    "research_evidence_from_tool_results",
    "research_source_ledger_from_tool_results",
]
