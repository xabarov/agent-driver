"""Web fetch and search built-in tools."""

from __future__ import annotations

import os
from hashlib import sha256
from typing import Any

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
    _error_message,
    _extract_mode,
    _extract_og_metadata,
    _extract_payload_text,
    _extract_pdf_text,
    _fetch_url_bytes_with_retry,
    _fetch_url_text_with_retry,
    _is_text_content_type,
    _mock_fetch_payload,
    _validate_http_url,
)
from agent_driver.tools.registry import ToolRegistry
from agent_driver.tools.builtin.web_search_backends import (  # noqa: F401
    _build_result_preview_urls,
    _normalize_mock_results,
    _search_payload,
    _relax_web_search_query,
    _duckduckgo_html_search,
    _resolve_search_backend,
    _parse_duckduckgo_html,
    _normalize_search_href,
    _is_retryable_search_exception,
    _fetch_ddg_with_retry,
    _tavily_search,
    _brave_search,
    _DEFAULT_MAX_BYTES,
)

_WEB_FETCH_TOOL = "web_fetch"
_WEB_SEARCH_TOOL = "web_search"
_SOURCE_READ_TOOL = "source_read"
_PDF_READ_TOOL = "pdf_read"
_BROWSER_READ_TOOL = "browser_read"
_DEFAULT_TIMEOUT_SECONDS = 15.0
_DEFAULT_MAX_RESULTS = 5
_DEFAULT_PREVIEW_CHARS = 1_500
_WEB_FETCH_MAX_CHARS_CAP = 8_000
_WEB_FETCH_EXCERPT_CHARS = 2_000
_PDF_READ_MAX_BYTES_CAP = 5_000_000


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


__all__ = ["register_web_tools"]
