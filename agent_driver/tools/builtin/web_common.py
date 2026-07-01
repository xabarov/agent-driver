"""Shared HTTP / URL / HTML utilities for the builtin web tools.

Leaf helpers (HTTP fetch + retry, URL validation, HTML/text extraction,
numeric arg parsing) used across web_fetch / web_search / source_read /
pdf_read / browser_read — extracted from web.py so the tool handlers read
against a small shared core. Pure utilities; no dependency back on web.py.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
from dataclasses import dataclass
from html import unescape
from typing import Any
from urllib.parse import urlparse

import httpx

_DEFAULT_USER_AGENT = "agent-driver/0.1"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_BLOCKED_STATUS_CODES = {401, 403}
_TEXT_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/xhtml+xml",
)
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)
_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_META_ATTR_RE = re.compile(
    r'([A-Za-z_:.-]+)\s*=\s*("([^"]*)"|\'([^\']*)\'|([^\s>]+))',
    re.IGNORECASE,
)


@dataclass
class _HttpPayload:
    url: str
    status_code: int
    content_type: str
    text: str
    bytes_total: int
    bytes_loaded: int


def _error_message(exc: Exception) -> str:
    text = str(exc).strip()
    if text:
        return text
    return repr(exc)


async def _fetch_url_text(
    *,
    url: str,
    timeout_seconds: float,
    max_bytes: int,
) -> _HttpPayload:
    headers = {"User-Agent": _DEFAULT_USER_AGENT}
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=timeout_seconds
    ) as client:
        response = await client.get(url, headers=headers)
    if response.status_code not in _BLOCKED_STATUS_CODES:
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


def _mock_fetch_payload(url: str, args: dict[str, Any]) -> _HttpPayload:
    status_code = _as_int(args.get("mock_status_code"), default=200, minimum=100)
    if status_code > 599:
        raise ValueError("mock_status_code must be <= 599")
    content = args.get("mock_content")
    if not isinstance(content, str):
        content = ""
    content_type = args.get("mock_content_type")
    if not isinstance(content_type, str) or not content_type.strip():
        content_type = "text/html; charset=utf-8"
    size = len(content.encode("utf-8"))
    return _HttpPayload(
        url=url,
        status_code=status_code,
        content_type=content_type.lower(),
        text=content,
        bytes_total=size,
        bytes_loaded=size,
    )


async def _fetch_url_text_with_retry(
    *,
    url: str,
    timeout_seconds: float,
    max_bytes: int,
) -> _HttpPayload:
    last_error: httpx.TimeoutException | None = None
    for attempt in range(3):
        try:
            return await _fetch_url_text(
                url=url,
                timeout_seconds=timeout_seconds,
                max_bytes=max_bytes,
            )
        except httpx.TimeoutException as exc:
            last_error = exc
            if attempt == 2:
                raise
            await asyncio.sleep(0.5 + (0.5 * attempt))
    if last_error is not None:
        raise last_error
    raise RuntimeError("web fetch failed without specific exception")


async def _fetch_url_bytes_with_retry(
    *,
    url: str,
    timeout_seconds: float,
    max_bytes: int,
) -> tuple[bytes, int, int]:
    last_error: httpx.TimeoutException | None = None
    headers = {"User-Agent": _DEFAULT_USER_AGENT}
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=timeout_seconds,
            ) as client:
                response = await client.get(url, headers=headers)
            if response.status_code not in _BLOCKED_STATUS_CODES:
                response.raise_for_status()
            content = response.content
            bytes_total = len(content)
            if len(content) > max_bytes:
                content = content[:max_bytes]
            return content, response.status_code, bytes_total
        except httpx.TimeoutException as exc:
            last_error = exc
            if attempt == 2:
                raise
            await asyncio.sleep(0.5 + (0.5 * attempt))
    if last_error is not None:
        raise last_error
    raise RuntimeError("web fetch failed without specific exception")


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
            if addr is not None and (
                addr.is_private
                or addr.is_loopback
                or addr.is_link_local
                or addr.is_reserved
                or addr.is_multicast
                or addr.is_unspecified
            ):
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
    cleaned = "\n".join(
        line.strip() for line in normalized.splitlines() if line.strip()
    )
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
            else (
                match.group(4)
                if match.group(4) is not None
                else match.group(5) if match.group(5) is not None else ""
            )
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


@dataclass(frozen=True)
class _PdfExtraction:
    """Page-aware text from a PDF, via the optional ``[pdf]`` extra (pypdf).

    ``pages`` holds ``(page_number, text)`` for the clamped requested range. A
    scanned/image-only PDF yields empty per-page strings, so ``has_text`` is
    False and the caller must not treat it as verified textual evidence.
    """

    total_pages: int
    pages: tuple[tuple[int, str], ...]
    parse_error: str | None = None

    @property
    def has_text(self) -> bool:
        return any(text.strip() for _, text in self.pages)


def _extract_pdf_text(
    data: bytes, *, page_start: int, page_end: int
) -> _PdfExtraction | None:
    """Extract per-page text from PDF bytes using the optional ``[pdf]`` extra.

    Returns ``None`` when pypdf is not installed — the caller then reports
    ``text_extraction_unavailable`` and does not present the PDF as verified
    evidence (degrades gracefully, keeping the core dependency-light). When
    pypdf is present the page range is clamped to the document and each page's
    text is extracted; a malformed PDF surfaces ``parse_error`` and a scanned
    PDF surfaces empty per-page text.
    """
    try:
        from pypdf import PdfReader  # optional [pdf] extra
    except ImportError:
        return None
    import io

    try:
        reader = PdfReader(io.BytesIO(data))
        total_pages = len(reader.pages)
    except Exception as exc:  # malformed structure past the magic-byte check
        return _PdfExtraction(total_pages=0, pages=(), parse_error=_error_message(exc))
    if total_pages == 0:
        return _PdfExtraction(total_pages=0, pages=())
    start = max(1, page_start)
    end = min(page_end, total_pages)
    pages: list[tuple[int, str]] = []
    for number in range(start, end + 1):
        try:
            text = reader.pages[number - 1].extract_text() or ""
        except Exception:  # one bad page must not fail the whole read
            text = ""
        pages.append((number, text.strip()))
    return _PdfExtraction(total_pages=total_pages, pages=tuple(pages))
