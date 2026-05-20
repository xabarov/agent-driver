"""Web fetch and search built-in tools."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from html import unescape
import ipaddress
import os
import re
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

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
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
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
_RESULT_LINK_ALT_RE = re.compile(
    r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_META_ATTR_RE = re.compile(
    r'([A-Za-z_:.-]+)\s*=\s*("([^"]*)"|\'([^\']*)\'|([^\s>]+))',
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class _HttpPayload:
    url: str
    status_code: int
    content_type: str
    text: str
    bytes_total: int
    bytes_loaded: int


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
                "extract_mode": {
                    "type": "string",
                    "enum": ["raw", "text", "markdown"],
                    "description": "Response extraction mode",
                },
                "allow_private_host": {
                    "type": "boolean",
                    "description": "Allow localhost/private host targets",
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
    url = _validate_http_url(
        args.get("url"),
        allow_private_host=bool(args.get("allow_private_host", False)),
    )
    timeout_seconds = _as_float(
        args.get("timeout_seconds"), default=_DEFAULT_TIMEOUT_SECONDS, minimum=0.1
    )
    max_bytes = _as_int(args.get("max_bytes"), default=_DEFAULT_MAX_BYTES, minimum=256)
    max_chars = _as_int(args.get("max_chars"), default=5_000, minimum=64)
    extract_mode = _extract_mode(args.get("extract_mode"))
    try:
        payload = await _fetch_url_text(
            url=url,
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes,
        )
    except (httpx.HTTPError, ValueError) as exc:
        raise ValueError(f"web_fetch failed: {exc}") from exc
    if not _is_text_content_type(payload.content_type):
        raise ValueError(f"unsupported content type: {payload.content_type}")
    extracted = _extract_payload_text(
        text=payload.text,
        content_type=payload.content_type,
        mode=extract_mode,
    )
    metadata = (
        _extract_og_metadata(payload.text) if "html" in payload.content_type else {}
    )
    content = extracted[:max_chars]
    truncated = len(extracted) > max_chars
    return {
        "summary": (
            f"fetched {payload.url} "
            f"(status={payload.status_code}, chars={len(content)})"
        ),
        "url": payload.url,
        "status_code": payload.status_code,
        "content_type": payload.content_type,
        "extract_mode": extract_mode,
        "bytes_total": payload.bytes_total,
        "bytes_loaded": payload.bytes_loaded,
        "bytes_truncated": payload.bytes_loaded < payload.bytes_total,
        "metadata": metadata,
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
        return _search_payload(
            query=query,
            source="mock",
            rows=normalized,
            max_results=max_results,
            parse_status="ok",
        )
    backend = _resolve_search_backend()
    if backend == "tavily":
        key = os.environ.get("TAVILY_API_KEY")
        if key:
            tavily = await _tavily_search(
                query=query,
                max_results=max_results,
                timeout_seconds=timeout_seconds,
                api_key=key,
            )
            if tavily is not None:
                return tavily
    elif backend == "brave":
        key = os.environ.get("BRAVE_SEARCH_API_KEY")
        if key:
            brave = await _brave_search(
                query=query,
                max_results=max_results,
                timeout_seconds=timeout_seconds,
                api_key=key,
            )
            if brave is not None:
                return brave
    search_url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
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
    parse_status = "ok" if rows else "parse_failed"
    result = _search_payload(
        query=query,
        source="duckduckgo_html",
        rows=rows,
        max_results=max_results,
        parse_status=parse_status,
    )
    if not rows:
        result["diagnostic"] = {
            "status": "no_results_parsed",
            "html_chars": len(payload.text),
            "content_type": payload.content_type,
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
    raw = str(os.environ.get("AGENT_DRIVER_WEB_SEARCH_BACKEND") or "ddg").strip().lower()
    if raw in {"ddg", "tavily", "brave"}:
        return raw
    return "ddg"


def _error_message(exc: Exception) -> str:
    text = str(exc).strip()
    if text:
        return text
    return repr(exc)


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
    bytes_total = len(content)
    if len(content) > max_bytes:
        content = content[:max_bytes]
    bytes_loaded = len(content)
    text = content.decode(response.encoding or "utf-8", errors="replace")
    return _HttpPayload(
        url=str(response.url),
        status_code=response.status_code,
        content_type=response.headers.get("content-type", "").lower(),
        text=text,
        bytes_total=bytes_total,
        bytes_loaded=bytes_loaded,
    )


def _is_retryable_search_exception(exc: Exception) -> bool:
    if isinstance(exc, httpx.ReadTimeout):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return False


async def _fetch_ddg_with_retry(
    *, search_url: str, timeout_seconds: float, max_bytes: int
) -> _HttpPayload:
    last_error: Exception | None = None
    for attempt in range(2):
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
    rows = _normalize_mock_results(web_payload.get("results", []), max_results=max_results)
    return _search_payload(
        query=query,
        source="brave",
        rows=rows,
        max_results=max_results,
        parse_status="ok" if rows else "parse_failed",
    )


def _validate_http_url(raw: Any, *, allow_private_host: bool = False) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("url must be a non-empty string")
    value = raw.strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("url scheme must be http or https")
    if not parsed.netloc:
        raise ValueError("url must include host")
    if not allow_private_host:
        host = (parsed.hostname or "").strip().lower()
        if host in _LOCAL_HOSTS:
            raise ValueError("private/localhost hosts are blocked by policy")
        if host:
            try:
                addr = ipaddress.ip_address(host)
            except ValueError:
                addr = None
            if addr is not None and (addr.is_private or addr.is_loopback):
                raise ValueError("private/localhost hosts are blocked by policy")
    return value


def _is_text_content_type(content_type: str) -> bool:
    if not content_type:
        return True
    base = content_type.split(";", maxsplit=1)[0].strip().lower()
    return any(base.startswith(prefix) for prefix in _TEXT_CONTENT_TYPES)


def _clean_html_text(raw: str) -> str:
    no_tags = _TAG_RE.sub(" ", raw)
    return " ".join(unescape(no_tags).split())


def _extract_mode(raw: Any) -> str:
    value = str(raw or "raw").strip().lower()
    if value not in {"raw", "text", "markdown"}:
        raise ValueError("extract_mode must be one of: raw, text, markdown")
    return value


def _extract_payload_text(*, text: str, content_type: str, mode: str) -> str:
    if mode == "raw":
        return text
    is_html = "html" in content_type
    if not is_html:
        return text
    html_without_embeds = _SCRIPT_STYLE_RE.sub(" ", text)
    metadata = _extract_og_metadata(html_without_embeds)
    if mode == "text":
        body = _clean_html_text(html_without_embeds)
        return _prepend_metadata_text(body=body, metadata=metadata)
    normalized = _TAG_RE.sub("\n", html_without_embeds)
    cleaned = "\n".join(line.strip() for line in normalized.splitlines() if line.strip())
    body = unescape(cleaned)
    return _prepend_metadata_text(body=body, metadata=metadata)


def _extract_og_metadata(raw_html: str) -> dict[str, str]:
    fields = {
        "og:title": "title",
        "og:description": "description",
        "og:url": "url",
        "article:published_time": "published_time",
    }
    metadata: dict[str, str] = {}
    html = _SCRIPT_STYLE_RE.sub(" ", raw_html)
    for match in _META_TAG_RE.finditer(html):
        attrs = _extract_tag_attributes(match.group(0))
        key = str(attrs.get("property") or attrs.get("name") or "").strip().lower()
        if key not in fields:
            continue
        content = str(attrs.get("content") or "").strip()
        if not content:
            continue
        target_key = fields[key]
        if target_key not in metadata:
            metadata[target_key] = unescape(content)
    return metadata


def _extract_tag_attributes(raw_tag: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in _META_ATTR_RE.finditer(raw_tag):
        name = str(match.group(1) or "").strip().lower()
        value = (
            match.group(3)
            if match.group(3) is not None
            else match.group(4)
            if match.group(4) is not None
            else match.group(5)
            if match.group(5) is not None
            else ""
        )
        if name:
            attrs[name] = value
    return attrs


def _prepend_metadata_text(*, body: str, metadata: dict[str, str]) -> str:
    lines: list[str] = []
    if metadata.get("title"):
        lines.append(f"Title: {metadata['title']}")
    if metadata.get("description"):
        lines.append(f"Description: {metadata['description']}")
    if metadata.get("url"):
        lines.append(f"URL: {metadata['url']}")
    if metadata.get("published_time"):
        lines.append(f"Published: {metadata['published_time']}")
    if not lines:
        return body
    meta_text = "\n".join(lines)
    body_text = body.strip()
    if body_text:
        return f"{meta_text}\n{body_text}"
    return meta_text


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
