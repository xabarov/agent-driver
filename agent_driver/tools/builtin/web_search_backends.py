"""Web-search backends + DuckDuckGo-HTML parsing / result-normalization helpers
(extracted from web.py).

Leaf module: everything behind the ``web_search`` handler except the handler +
manifest — the Tavily/Brave/DDG backends, DDG-HTML parsing, href normalization,
retry, and preview formatting. Self-contained (httpx/stdlib + web_common), no
back-edge into web.py — one-way (web -> web_search_backends).
"""

from __future__ import annotations
import asyncio
import os
import re
from html import unescape
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
import httpx
from agent_driver.tools.builtin.web_common import (
    _clean_html_text,
    _error_message,
    _fetch_url_text,
    _HttpPayload,
)

_DEFAULT_MAX_BYTES = 150_000
_RESULT_LINK_RE = re.compile(
    r'<a[^>]+class="[^"]*result-link[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_RESULT_LINK_ALT_RE = re.compile(
    r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_SITE_OPERATOR_RE = re.compile(r"\bsite:\S+\b", re.IGNORECASE)


def _relax_web_search_query(query: str) -> str | None:
    """Drop site: operator and keep domain as plain keywords for DDG html backend."""
    if not _SITE_OPERATOR_RE.search(query):
        return None
    site_match = re.search(r"\bsite:(\S+)\b", query, flags=re.IGNORECASE)
    site_token = site_match.group(1).strip() if site_match else ""
    relaxed = _SITE_OPERATOR_RE.sub("", query).strip()
    relaxed = re.sub(r"\s+", " ", relaxed)
    if site_token and site_token.lower() not in relaxed.lower():
        relaxed = f"{relaxed} {site_token}".strip()
    if not relaxed or relaxed == query:
        return None
    return relaxed


async def _duckduckgo_html_search(
    *,
    query: str,
    max_results: int,
    timeout_seconds: float,
    original_query: str | None = None,
) -> dict[str, Any]:
    """Run DuckDuckGo html search; retry without site: when DDG returns no parseable hits."""
    search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    try:
        payload = await _fetch_ddg_with_retry(
            search_url=search_url,
            timeout_seconds=timeout_seconds,
            max_bytes=_DEFAULT_MAX_BYTES,
        )
    except Exception as exc:  # noqa: BLE001 - fallback message normalization
        return _search_payload(
            query=query,
            source="duckduckgo_html",
            rows=[],
            max_results=max_results,
            parse_status="upstream_error",
            summary=f"web_search unavailable: {_error_message(exc)}",
        )
    rows = _parse_duckduckgo_html(payload.text, max_results=max_results)
    if not rows:
        relaxed = _relax_web_search_query(query)
        if relaxed is not None:
            return await _duckduckgo_html_search(
                query=relaxed,
                max_results=max_results,
                timeout_seconds=timeout_seconds,
                original_query=original_query or query,
            )
    parse_status = "ok" if rows else "parse_failed"
    source_query = original_query or query
    summary = None
    if rows and original_query and original_query != query:
        summary = (
            f"{len(rows)} results for '{query}' via duckduckgo_html "
            f"(relaxed from '{original_query}')"
        )
    result = _search_payload(
        query=query,
        source="duckduckgo_html",
        rows=rows,
        max_results=max_results,
        parse_status=parse_status,
        summary=summary,
    )
    if original_query and original_query != query:
        result["query_original"] = original_query
        result["query_relaxation"] = "stripped_site_operator"
    if not rows:
        result["diagnostic"] = {
            "status": "no_results_parsed",
            "html_chars": len(payload.text),
            "content_type": payload.content_type,
            "source_query": source_query,
        }
    return result


def _search_payload(
    *,
    query: str,
    source: str,
    rows: list[dict[str, str]],
    max_results: int,
    parse_status: str,
    summary: str | None = None,
) -> dict[str, Any]:
    truncated = len(rows) >= max_results
    payload_summary = summary or f"{len(rows)} results for '{query}' via {source}"
    preview_urls = _build_result_preview_urls(rows)
    return {
        "summary": payload_summary,
        "query": query,
        "source": source,
        "results": rows,
        "result_preview_urls": preview_urls,
        "returned_count": len(rows),
        "max_results": max_results,
        "truncated": truncated,
        "parse_status": parse_status,
    }


def _build_result_preview_urls(rows: list[dict[str, str]]) -> list[str]:
    previews: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        if not url:
            continue
        snippet = str(row.get("snippet") or "").strip()
        if snippet:
            snippet_short = snippet[:80]
            previews.append(f"{url} — {snippet_short}")
        else:
            previews.append(url)
        if len(previews) >= 3:
            break
    return previews


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


def _resolve_search_backend() -> str:
    raw = (
        str(os.environ.get("AGENT_DRIVER_WEB_SEARCH_BACKEND") or "ddg").strip().lower()
    )
    if raw in {"ddg", "tavily", "brave"}:
        return raw
    return "ddg"


def _parse_duckduckgo_html(html: str, *, max_results: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for match in _RESULT_LINK_RE.finditer(html):
        href = _normalize_search_href(unescape(match.group(1)).strip())
        title_html = match.group(2)
        title = _clean_html_text(title_html)
        if not href:
            continue
        rows.append({"title": title, "url": href, "snippet": ""})
        if len(rows) >= max_results:
            break
    if rows:
        return rows
    for match in _RESULT_LINK_ALT_RE.finditer(html):
        href = _normalize_search_href(unescape(match.group(1)).strip())
        title_html = match.group(2)
        title = _clean_html_text(title_html)
        if not href:
            continue
        rows.append({"title": title, "url": href, "snippet": ""})
        if len(rows) >= max_results:
            break
    return rows


def _normalize_search_href(raw_href: str) -> str:
    href = raw_href.strip()
    if href.startswith("//"):
        href = f"https:{href}"
    parsed = urlparse(href)
    if "duckduckgo.com" in (parsed.netloc or "") and parsed.path.startswith("/l/"):
        query = parse_qs(parsed.query)
        uddg_values = query.get("uddg")
        if isinstance(uddg_values, list) and uddg_values:
            target = unquote(str(uddg_values[0]).strip())
            if target:
                return target
    return href


def _is_retryable_search_exception(exc: Exception) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return False


async def _fetch_ddg_with_retry(
    *, search_url: str, timeout_seconds: float, max_bytes: int
) -> _HttpPayload:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            return await _fetch_url_text(
                url=search_url,
                timeout_seconds=timeout_seconds,
                max_bytes=max_bytes,
            )
        except Exception as exc:  # noqa: BLE001 - retry envelope
            if not _is_retryable_search_exception(exc) or attempt == 1:
                raise
            last_error = exc
            await asyncio.sleep(0.5 + (0.5 * attempt))
    if last_error is not None:
        raise last_error
    raise RuntimeError("ddg search failed without specific exception")


async def _tavily_search(
    *,
    query: str,
    max_results: int,
    timeout_seconds: float,
    api_key: str,
) -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"query": query, "max_results": max_results},
            )
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    raw_payload = response.json()
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    rows = _normalize_mock_results(payload.get("results", []), max_results=max_results)
    return _search_payload(
        query=query,
        source="tavily",
        rows=rows,
        max_results=max_results,
        parse_status="ok" if rows else "parse_failed",
    )


async def _brave_search(
    *,
    query: str,
    max_results: int,
    timeout_seconds: float,
    api_key: str,
) -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={
                    "X-Subscription-Token": api_key,
                    "Accept": "application/json",
                },
                params={"q": query, "count": max_results},
            )
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    raw_payload = response.json()
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    web_payload = payload.get("web", {}) if isinstance(payload.get("web"), dict) else {}
    rows = _normalize_mock_results(
        web_payload.get("results", []), max_results=max_results
    )
    return _search_payload(
        query=query,
        source="brave",
        rows=rows,
        max_results=max_results,
        parse_status="ok" if rows else "parse_failed",
    )
