"""Web fetch and search built-in tools."""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import re
from typing import Any
from urllib.parse import quote_plus
from urllib.parse import urlparse

import httpx

from agent_driver.contracts import (
    ApprovalMode,
    SideEffectClass,
    ToolManifest,
    ToolRisk,
)
from agent_driver.tools.registry import ToolRegistry

_WEB_FETCH_TOOL = "web_fetch"
_WEB_SEARCH_TOOL = "web_search"
_DEFAULT_TIMEOUT_SECONDS = 10.0
_DEFAULT_MAX_BYTES = 150_000
_DEFAULT_MAX_RESULTS = 5
_DEFAULT_PREVIEW_CHARS = 1_500
_DEFAULT_USER_AGENT = "agent-driver/0.1"
_TEXT_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/xhtml+xml",
)
_RESULT_LINK_RE = re.compile(
    r'<a[^>]+class="[^"]*result-link[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True, slots=True)
class _HttpPayload:
    url: str
    status_code: int
    content_type: str
    text: str


def register_web_tools(registry: ToolRegistry) -> None:
    """Register built-in web fetch/search tools."""
    registry.register(_web_fetch_manifest(), _web_fetch_handler)
    registry.register(_web_search_manifest(), _web_search_handler)


def _web_fetch_manifest() -> ToolManifest:
    return ToolManifest(
        name=_WEB_FETCH_TOOL,
        description=(
            "Fetch text content from HTTP(S) URL with safety limits and metadata."
        ),
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.EXTERNAL_ACTION,
        approval_mode=ApprovalMode.ON_POLICY_MATCH,
        timeout_seconds=15.0,
        output_char_budget=9000,
        idempotent=True,
        args_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "HTTP(S) URL"},
                "timeout_seconds": {
                    "type": "number",
                    "minimum": 0.1,
                    "maximum": 60,
                    "description": "Per-request timeout",
                },
                "max_bytes": {
                    "type": "integer",
                    "minimum": 256,
                    "maximum": 1_000_000,
                    "description": "Response byte cap before decode",
                },
                "max_chars": {
                    "type": "integer",
                    "minimum": 64,
                    "maximum": 50_000,
                    "description": "Maximum returned content chars",
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
        output_type="json",
    )


def _web_search_manifest() -> ToolManifest:
    return ToolManifest(
        name=_WEB_SEARCH_TOOL,
        description=(
            "Search the public web and return normalized result list "
            "(title, url, snippet)."
        ),
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.EXTERNAL_ACTION,
        approval_mode=ApprovalMode.ON_POLICY_MATCH,
        timeout_seconds=15.0,
        output_char_budget=9000,
        idempotent=True,
        args_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": "Maximum normalized results",
                },
                "timeout_seconds": {
                    "type": "number",
                    "minimum": 0.1,
                    "maximum": 60,
                    "description": "Search request timeout",
                },
                "mock_results": {
                    "type": "array",
                    "description": (
                        "Optional offline result rows; if passed, no network call"
                    ),
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        output_type="json",
    )


async def _web_fetch_handler(args: dict[str, Any]) -> dict[str, Any]:
    url = _validate_http_url(args.get("url"))
    timeout_seconds = _as_float(
        args.get("timeout_seconds"), default=_DEFAULT_TIMEOUT_SECONDS, minimum=0.1
    )
    max_bytes = _as_int(args.get("max_bytes"), default=_DEFAULT_MAX_BYTES, minimum=256)
    max_chars = _as_int(args.get("max_chars"), default=5_000, minimum=64)
    payload = await _fetch_url_text(
        url=url,
        timeout_seconds=timeout_seconds,
        max_bytes=max_bytes,
    )
    if not _is_text_content_type(payload.content_type):
        raise ValueError(f"unsupported content type: {payload.content_type}")
    content = payload.text[:max_chars]
    truncated = len(payload.text) > max_chars
    return {
        "summary": (
            f"fetched {payload.url} "
            f"(status={payload.status_code}, chars={len(content)})"
        ),
        "url": payload.url,
        "status_code": payload.status_code,
        "content_type": payload.content_type,
        "content": content,
        "truncated": truncated,
    }


async def _web_search_handler(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")
    max_results = _as_int(
        args.get("max_results"), default=_DEFAULT_MAX_RESULTS, minimum=1
    )
    timeout_seconds = _as_float(
        args.get("timeout_seconds"), default=_DEFAULT_TIMEOUT_SECONDS, minimum=0.1
    )
    mock_rows = args.get("mock_results")
    if isinstance(mock_rows, list):
        normalized = _normalize_mock_results(mock_rows, max_results=max_results)
        return _search_payload(query=query, source="mock", rows=normalized)
    search_url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
    payload = await _fetch_url_text(
        url=search_url,
        timeout_seconds=timeout_seconds,
        max_bytes=_DEFAULT_MAX_BYTES,
    )
    rows = _parse_duckduckgo_html(payload.text, max_results=max_results)
    return _search_payload(query=query, source="duckduckgo_html", rows=rows)


def _search_payload(
    *, query: str, source: str, rows: list[dict[str, str]]
) -> dict[str, Any]:
    return {
        "summary": f"{len(rows)} results for '{query}' via {source}",
        "query": query,
        "source": source,
        "results": rows,
    }


def _normalize_mock_results(
    rows: list[Any], *, max_results: int
) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        url = str(row.get("url") or "").strip()
        snippet = str(row.get("snippet") or "").strip()
        if not title and not url:
            continue
        normalized.append({"title": title, "url": url, "snippet": snippet})
        if len(normalized) >= max_results:
            break
    return normalized


def _parse_duckduckgo_html(html: str, *, max_results: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for match in _RESULT_LINK_RE.finditer(html):
        href = unescape(match.group(1)).strip()
        title_html = match.group(2)
        title = _clean_html_text(title_html)
        if not href:
            continue
        rows.append({"title": title, "url": href, "snippet": ""})
        if len(rows) >= max_results:
            break
    return rows


async def _fetch_url_text(
    *,
    url: str,
    timeout_seconds: float,
    max_bytes: int,
) -> _HttpPayload:
    headers = {"User-Agent": _DEFAULT_USER_AGENT}
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout_seconds) as client:
        response = await client.get(url, headers=headers)
    response.raise_for_status()
    content = response.content
    if len(content) > max_bytes:
        content = content[:max_bytes]
    text = content.decode(response.encoding or "utf-8", errors="replace")
    return _HttpPayload(
        url=str(response.url),
        status_code=response.status_code,
        content_type=response.headers.get("content-type", "").lower(),
        text=text,
    )


def _validate_http_url(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("url must be a non-empty string")
    value = raw.strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("url scheme must be http or https")
    if not parsed.netloc:
        raise ValueError("url must include host")
    return value


def _is_text_content_type(content_type: str) -> bool:
    if not content_type:
        return True
    base = content_type.split(";", maxsplit=1)[0].strip().lower()
    return any(base.startswith(prefix) for prefix in _TEXT_CONTENT_TYPES)


def _clean_html_text(raw: str) -> str:
    no_tags = _TAG_RE.sub(" ", raw)
    return " ".join(unescape(no_tags).split())


def _as_int(raw: Any, *, default: int, minimum: int) -> int:
    if raw is None:
        return default
    value = int(raw)
    if value < minimum:
        raise ValueError(f"value must be >= {minimum}")
    return value


def _as_float(raw: Any, *, default: float, minimum: float) -> float:
    if raw is None:
        return default
    value = float(raw)
    if value < minimum:
        raise ValueError(f"value must be >= {minimum}")
    return value


__all__ = ["register_web_tools"]
