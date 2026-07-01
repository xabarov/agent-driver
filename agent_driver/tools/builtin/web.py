"""Web fetch and search built-in tools."""

from __future__ import annotations

import asyncio
import os
import re
from hashlib import sha256
from html import unescape
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx

from agent_driver.contracts import (
    ApprovalMode,
    SideEffectClass,
    ToolManifest,
    ToolRisk,
)
from agent_driver.tools.builtin.web_common import (
    _BLOCKED_STATUS_CODES,
    _as_float,
    _as_int,
    _clean_html_text,
    _error_message,
    _extract_mode,
    _extract_og_metadata,
    _extract_payload_text,
    _extract_pdf_text,
    _fetch_url_bytes_with_retry,
    _fetch_url_text,
    _fetch_url_text_with_retry,
    _HttpPayload,
    _is_text_content_type,
    _mock_fetch_payload,
    _validate_http_url,
)
from agent_driver.tools.registry import ToolRegistry

_WEB_FETCH_TOOL = "web_fetch"
_WEB_SEARCH_TOOL = "web_search"
_SOURCE_READ_TOOL = "source_read"
_PDF_READ_TOOL = "pdf_read"
_BROWSER_READ_TOOL = "browser_read"
_DEFAULT_TIMEOUT_SECONDS = 15.0
_DEFAULT_MAX_BYTES = 150_000
_DEFAULT_MAX_RESULTS = 5
_DEFAULT_PREVIEW_CHARS = 1_500
_WEB_FETCH_MAX_CHARS_CAP = 8_000
_WEB_FETCH_EXCERPT_CHARS = 2_000
_PDF_READ_MAX_BYTES_CAP = 5_000_000
_RESULT_LINK_RE = re.compile(
    r'<a[^>]+class="[^"]*result-link[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_RESULT_LINK_ALT_RE = re.compile(
    r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_SITE_OPERATOR_RE = re.compile(r"\bsite:\S+\b", re.IGNORECASE)


def register_web_tools(registry: ToolRegistry) -> None:
    """Register built-in web fetch/search tools."""
    registry.register(_web_fetch_manifest(), _web_fetch_handler)
    registry.register(_web_search_manifest(), _web_search_handler)
    registry.register(_source_read_manifest(), _source_read_handler)
    registry.register(_pdf_read_manifest(), _pdf_read_handler)
    registry.register(_browser_read_manifest(), _browser_read_handler)


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
                "mock_status_code": {
                    "type": "integer",
                    "minimum": 100,
                    "maximum": 599,
                    "description": (
                        "Optional offline HTTP status for deterministic tests; "
                        "when present, no network call is made"
                    ),
                },
                "mock_content": {
                    "type": "string",
                    "description": "Optional offline response body for tests",
                },
                "mock_content_type": {
                    "type": "string",
                    "description": "Optional offline content type for tests",
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


def _source_read_manifest() -> ToolManifest:
    return ToolManifest(
        name=_SOURCE_READ_TOOL,
        description=(
            "Read a cited source URL for hard Deep Research verification. "
            "Uses the same HTTP safety limits as web_fetch and returns text content."
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
                "url": {"type": "string", "description": "HTTP(S) source URL"},
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
                "mock_status_code": {
                    "type": "integer",
                    "minimum": 100,
                    "maximum": 599,
                    "description": "Optional offline status for deterministic tests",
                },
                "mock_content": {
                    "type": "string",
                    "description": "Optional offline response body for tests",
                },
                "mock_content_type": {
                    "type": "string",
                    "description": "Optional offline content type for tests",
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
        output_type="json",
    )


def _pdf_read_manifest() -> ToolManifest:
    return ToolManifest(
        name=_PDF_READ_TOOL,
        description=(
            "Validate and read a PDF source for hard Deep Research. Extracts "
            "page-aware text when the optional [pdf] extra is installed and "
            "returns per-page citations; scanned PDFs, missing extractor, or "
            "PDFs without extractable text are not treated as verified evidence."
        ),
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.EXTERNAL_ACTION,
        approval_mode=ApprovalMode.ON_POLICY_MATCH,
        timeout_seconds=20.0,
        output_char_budget=9000,
        idempotent=True,
        args_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "HTTP(S) PDF URL"},
                "page_start": {"type": "integer", "minimum": 1},
                "page_end": {"type": "integer", "minimum": 1},
                "timeout_seconds": {
                    "type": "number",
                    "minimum": 0.1,
                    "maximum": 60,
                },
                "max_bytes": {
                    "type": "integer",
                    "minimum": 256,
                    "maximum": _PDF_READ_MAX_BYTES_CAP,
                },
                "allow_private_host": {"type": "boolean"},
                "mock_pdf_bytes": {
                    "type": "string",
                    "description": "Optional offline PDF bytes as latin-1 text.",
                },
                "mock_extracted_text": {
                    "type": "string",
                    "description": "Optional deterministic extracted PDF text.",
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
        output_type="json",
    )


def _browser_read_manifest() -> ToolManifest:
    return ToolManifest(
        name=_BROWSER_READ_TOOL,
        description=(
            "Hard-profile read-only rendered-page fallback. Current implementation "
            "uses the same URL safety checks as web_fetch and does not perform "
            "browser actions, cookies, typing, or private-network access."
        ),
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.EXTERNAL_ACTION,
        approval_mode=ApprovalMode.ON_POLICY_MATCH,
        timeout_seconds=20.0,
        output_char_budget=9000,
        idempotent=True,
        args_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "HTTP(S) page URL"},
                "timeout_seconds": {
                    "type": "number",
                    "minimum": 0.1,
                    "maximum": 60,
                },
                "max_bytes": {
                    "type": "integer",
                    "minimum": 256,
                    "maximum": 1_000_000,
                },
                "max_chars": {
                    "type": "integer",
                    "minimum": 64,
                    "maximum": 50_000,
                },
                "mock_status_code": {
                    "type": "integer",
                    "minimum": 100,
                    "maximum": 599,
                },
                "mock_content": {"type": "string"},
                "mock_content_type": {"type": "string"},
                "fallback_reason": {
                    "type": "string",
                    "description": (
                        "Why source_read/pdf_read were insufficient and rendered "
                        "fallback is needed"
                    ),
                },
            },
            "required": ["url"],
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
    requested_max_chars = _as_int(args.get("max_chars"), default=5_000, minimum=64)
    max_chars = min(requested_max_chars, _WEB_FETCH_MAX_CHARS_CAP)
    extract_mode = _extract_mode(args.get("extract_mode"))
    if args.get("mock_status_code") is not None:
        payload = _mock_fetch_payload(url, args)
    else:
        try:
            payload = await _fetch_url_text_with_retry(
                url=url,
                timeout_seconds=timeout_seconds,
                max_bytes=max_bytes,
            )
        except httpx.TimeoutException as exc:
            return _web_fetch_unavailable_payload(
                url=url,
                extract_mode=extract_mode,
                timeout_seconds=timeout_seconds,
                max_chars=max_chars,
                reason=f"timeout: {_error_message(exc)}",
            )
        except (httpx.HTTPError, ValueError) as exc:
            raise ValueError(f"web_fetch failed: {exc}") from exc
    if not _is_text_content_type(payload.content_type):
        raise ValueError(f"unsupported content type: {payload.content_type}")
    if payload.status_code in _BLOCKED_STATUS_CODES:
        metadata = (
            _extract_og_metadata(payload.text) if "html" in payload.content_type else {}
        )
        return {
            "summary": (
                f"web_fetch blocked by upstream HTTP {payload.status_code} for "
                f"{payload.url}; try another search result, an official source, "
                "or a cached/reader URL."
            ),
            "url": payload.url,
            "status_code": payload.status_code,
            "content_type": payload.content_type,
            "extract_mode": extract_mode,
            "bytes_total": payload.bytes_total,
            "bytes_loaded": payload.bytes_loaded,
            "bytes_truncated": payload.bytes_loaded < payload.bytes_total,
            "metadata": metadata,
            "excerpt": "",
            "content": "",
            "truncated": False,
            "max_chars_applied": max_chars,
            "blocked": True,
        }
    extracted = _extract_payload_text(
        text=payload.text,
        content_type=payload.content_type,
        mode=extract_mode,
    )
    metadata = (
        _extract_og_metadata(payload.text) if "html" in payload.content_type else {}
    )
    content = extracted[:max_chars]
    excerpt = content[:_WEB_FETCH_EXCERPT_CHARS]
    truncated = len(extracted) > max_chars
    summary_parts = [
        f"fetched {payload.url} (status={payload.status_code}, chars={len(content)})"
    ]
    if isinstance(metadata, dict):
        title = metadata.get("title")
        if isinstance(title, str) and title.strip():
            summary_parts.append(f"title={title.strip()}")
        published = metadata.get("published_time")
        if isinstance(published, str) and published.strip():
            summary_parts.append(f"published={published.strip()}")
    return {
        "summary": "; ".join(summary_parts),
        "url": payload.url,
        "status_code": payload.status_code,
        "content_type": payload.content_type,
        "extract_mode": extract_mode,
        "bytes_total": payload.bytes_total,
        "bytes_loaded": payload.bytes_loaded,
        "bytes_truncated": payload.bytes_loaded < payload.bytes_total,
        "metadata": metadata,
        "excerpt": excerpt,
        "content": content,
        "truncated": truncated,
        "max_chars_applied": max_chars,
    }


async def _source_read_handler(args: dict[str, Any]) -> dict[str, Any]:
    payload = await _web_fetch_handler(args)
    content = str(payload.get("content") or "")
    return {
        **payload,
        "summary": f"source_read: {payload.get('summary', '')}",
        "source_read": True,
        "source_kind": "url",
        "verified_text": bool(content) and payload.get("blocked") is not True,
        "content_sha256": (
            sha256(content.encode("utf-8")).hexdigest() if content else ""
        ),
    }


async def _pdf_read_handler(args: dict[str, Any]) -> dict[str, Any]:
    url = _validate_http_url(
        args.get("url"),
        allow_private_host=bool(args.get("allow_private_host", False)),
    )
    page_start = _as_int(args.get("page_start"), default=1, minimum=1)
    page_end = _as_int(args.get("page_end"), default=page_start, minimum=page_start)
    max_bytes = min(
        _as_int(args.get("max_bytes"), default=_DEFAULT_MAX_BYTES, minimum=256),
        _PDF_READ_MAX_BYTES_CAP,
    )
    mock_pdf = args.get("mock_pdf_bytes")
    if isinstance(mock_pdf, str):
        data = mock_pdf.encode("latin-1", errors="replace")
        bytes_total = len(data)
        status_code = int(args.get("mock_status_code") or 200)
    else:
        timeout_seconds = _as_float(
            args.get("timeout_seconds"), default=_DEFAULT_TIMEOUT_SECONDS, minimum=0.1
        )
        try:
            data, status_code, bytes_total = await _fetch_url_bytes_with_retry(
                url=url,
                timeout_seconds=timeout_seconds,
                max_bytes=max_bytes,
            )
        except httpx.TimeoutException as exc:
            return _pdf_error_payload(
                url=url,
                page_start=page_start,
                page_end=page_end,
                error="timeout",
                detail=_error_message(exc),
            )
        except (httpx.HTTPError, ValueError) as exc:
            raise ValueError(f"pdf_read failed: {exc}") from exc
    if bytes_total > max_bytes:
        return _pdf_error_payload(
            url=url,
            page_start=page_start,
            page_end=page_end,
            error="pdf_too_large",
            detail=f"{bytes_total} bytes exceeds {max_bytes}",
            status_code=status_code,
        )
    if not data.startswith(b"%PDF"):
        return _pdf_error_payload(
            url=url,
            page_start=page_start,
            page_end=page_end,
            error="invalid_pdf",
            detail="missing PDF magic bytes",
            status_code=status_code,
        )
    mock_extracted = str(args.get("mock_extracted_text") or "")
    if mock_extracted.strip():
        return _pdf_verified_payload(
            url=url,
            status_code=status_code,
            page_start=page_start,
            page_end=page_end,
            bytes_total=bytes_total,
            bytes_loaded=len(data),
            text=mock_extracted,
            page_citations=[
                {"page": page, "url": url} for page in range(page_start, page_end + 1)
            ],
        )
    extraction = _extract_pdf_text(data, page_start=page_start, page_end=page_end)
    if extraction is None:
        return _pdf_unverified_payload(
            url=url,
            status_code=status_code,
            page_start=page_start,
            page_end=page_end,
            bytes_total=bytes_total,
            bytes_loaded=len(data),
            error="text_extraction_unavailable",
            summary=(
                f"pdf_read validated {url} but text extraction is unavailable "
                "(install the [pdf] extra); do not treat this PDF as verified "
                "textual evidence."
            ),
        )
    if extraction.parse_error:
        return _pdf_error_payload(
            url=url,
            page_start=page_start,
            page_end=page_end,
            error="pdf_parse_failed",
            detail=extraction.parse_error,
            status_code=status_code,
        )
    if not extraction.has_text:
        return _pdf_unverified_payload(
            url=url,
            status_code=status_code,
            page_start=page_start,
            page_end=page_end,
            bytes_total=bytes_total,
            bytes_loaded=len(data),
            error="no_extractable_text",
            summary=(
                f"pdf_read validated {url} but found no extractable text "
                "(likely a scanned PDF); do not treat this PDF as verified "
                "textual evidence."
            ),
        )
    pages_with_text = [(page, text) for page, text in extraction.pages if text.strip()]
    return _pdf_verified_payload(
        url=url,
        status_code=status_code,
        page_start=pages_with_text[0][0],
        page_end=pages_with_text[-1][0],
        bytes_total=bytes_total,
        bytes_loaded=len(data),
        text="\n\n".join(text for _, text in pages_with_text),
        page_citations=[{"page": page, "url": url} for page, _ in pages_with_text],
        total_pages=extraction.total_pages,
    )


async def _browser_read_handler(args: dict[str, Any]) -> dict[str, Any]:
    fallback_reason = str(
        args.get("fallback_reason") or "source_read_or_pdf_read_insufficient"
    ).strip()
    payload = await _web_fetch_handler(
        {
            **args,
            "extract_mode": "text",
            "allow_private_host": False,
        }
    )
    return {
        **payload,
        "summary": f"browser_read fallback: {payload.get('summary', '')}",
        "browser_read": True,
        "source_kind": "rendered_page",
        "status": "verified" if payload.get("blocked") is not True else "blocked",
        "fallback_reason": fallback_reason,
        "browser_fallback_reason": fallback_reason,
        "rendered": False,
        "browser_action_allowed": False,
        "screenshot_artifact": None,
    }


def _pdf_verified_payload(
    *,
    url: str,
    status_code: int | None,
    page_start: int,
    page_end: int,
    bytes_total: int,
    bytes_loaded: int,
    text: str,
    page_citations: list[dict[str, Any]],
    total_pages: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "summary": f"pdf_read extracted text from {url} pages {page_start}-{page_end}",
        "url": url,
        "status_code": status_code,
        "pdf_read": True,
        "source_kind": "pdf",
        "status": "verified",
        "page_start": page_start,
        "page_end": page_end,
        "bytes_total": bytes_total,
        "bytes_loaded": bytes_loaded,
        "text": text,
        "excerpt": text[:_WEB_FETCH_EXCERPT_CHARS],
        "page_citations": page_citations,
        "verified_text": True,
    }
    if total_pages is not None:
        payload["total_pages"] = total_pages
    return payload


def _pdf_unverified_payload(
    *,
    url: str,
    status_code: int | None,
    page_start: int,
    page_end: int,
    bytes_total: int,
    bytes_loaded: int,
    error: str,
    summary: str,
) -> dict[str, Any]:
    return {
        "summary": summary,
        "url": url,
        "status_code": status_code,
        "pdf_read": True,
        "source_kind": "pdf",
        "status": "partial",
        "page_start": page_start,
        "page_end": page_end,
        "bytes_total": bytes_total,
        "bytes_loaded": bytes_loaded,
        "text": "",
        "excerpt": "",
        "page_citations": [],
        "verified_text": False,
        "error": error,
    }


def _pdf_error_payload(
    *,
    url: str,
    page_start: int,
    page_end: int,
    error: str,
    detail: str,
    status_code: int | None = None,
) -> dict[str, Any]:
    return {
        "summary": f"pdf_read could not verify text for {url}: {error}",
        "url": url,
        "status_code": status_code,
        "pdf_read": True,
        "source_kind": "pdf",
        "status": "failed",
        "page_start": page_start,
        "page_end": page_end,
        "text": "",
        "excerpt": "",
        "page_citations": [],
        "verified_text": False,
        "error": error,
        "detail": detail,
    }


def _web_fetch_unavailable_payload(
    *,
    url: str,
    extract_mode: str,
    timeout_seconds: float,
    max_chars: int,
    reason: str,
) -> dict[str, Any]:
    return {
        "summary": (
            f"web_fetch unavailable for {url}: {reason}; try another search result, "
            "an official source, or a cached/reader URL."
        ),
        "url": url,
        "status_code": None,
        "content_type": "",
        "extract_mode": extract_mode,
        "bytes_total": 0,
        "bytes_loaded": 0,
        "bytes_truncated": False,
        "metadata": {},
        "excerpt": "",
        "content": "",
        "truncated": False,
        "max_chars_applied": max_chars,
        "timeout_seconds": timeout_seconds,
        "unavailable": True,
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
    return await _duckduckgo_html_search(
        query=query,
        max_results=max_results,
        timeout_seconds=timeout_seconds,
    )


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


__all__ = ["register_web_tools"]
