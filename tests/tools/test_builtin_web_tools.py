"""Tests for built-in web fetch and search tools."""

# pylint: disable=too-few-public-methods

from __future__ import annotations

import pytest
import httpx

from agent_driver.tools.builtin.web import register_web_tools
from agent_driver.tools.registry import ToolRegistry


class _DummyResponse:
    """Simple response stub used by patched AsyncClient."""

    def __init__(self, *, url: str, **kwargs) -> None:
        """Build response payload with optional override fields."""
        self.url = url
        self.status_code = kwargs.get("status_code", 200)
        self.content = kwargs.get("content", b"")
        self.headers = kwargs.get("headers", {})
        self.encoding = kwargs.get("encoding", "utf-8")

    def raise_for_status(self) -> None:
        """Raise when status code is an HTTP error."""
        if self.status_code >= 400:
            raise ValueError(f"http error {self.status_code}")


class _DummyClient:
    """Tiny async client double returning a preset response."""

    def __init__(self, response: _DummyResponse) -> None:
        self._response = response

    async def __aenter__(self) -> "_DummyClient":
        """Return self for async context manager protocol."""
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        """No-op async context manager exit."""
        return None

    async def get(
        self, url: str, headers: dict[str, str] | None = None
    ) -> _DummyResponse:
        """Return configured response for each GET request."""
        _ = (url, headers)
        return self._response


@pytest.mark.asyncio
async def test_web_fetch_returns_text_payload(monkeypatch) -> None:
    """web_fetch should return bounded text with metadata."""
    response = _DummyResponse(
        url="https://example.com",
        content=b"hello web tool",
        headers={"content-type": "text/plain; charset=utf-8"},
    )

    def _client_factory(*_args, **_kwargs):
        return _DummyClient(response)

    monkeypatch.setattr(
        "agent_driver.tools.builtin.web.httpx.AsyncClient", _client_factory
    )
    registry = ToolRegistry()
    register_web_tools(registry)
    tool = registry.get("web_fetch")
    assert tool is not None
    out = await tool.handler({"url": "https://example.com"})
    assert out["status_code"] == 200
    assert out["content"] == "hello web tool"
    assert out["truncated"] is False
    assert out["extract_mode"] == "raw"
    assert out["bytes_total"] == out["bytes_loaded"]


@pytest.mark.asyncio
async def test_source_read_wraps_web_fetch_payload() -> None:
    """source_read should expose hard-profile source verification reads."""
    registry = ToolRegistry()
    register_web_tools(registry)
    tool = registry.get("source_read")
    assert tool is not None

    out = await tool.handler(
        {
            "url": "https://example.com/source",
            "mock_status_code": 200,
            "mock_content": "source body",
            "mock_content_type": "text/plain",
        }
    )

    assert out["source_read"] is True
    assert out["source_kind"] == "url"
    assert out["verified_text"] is True
    assert out["content_sha256"]
    assert out["content"] == "source body"
    assert out["summary"].startswith("source_read:")


@pytest.mark.asyncio
async def test_pdf_read_validates_pdf_and_returns_mock_text() -> None:
    """pdf_read should validate magic bytes and expose page citation hints."""
    registry = ToolRegistry()
    register_web_tools(registry)
    tool = registry.get("pdf_read")
    assert tool is not None

    out = await tool.handler(
        {
            "url": "https://example.com/paper.pdf",
            "mock_pdf_bytes": "%PDF-1.4\nbody",
            "mock_extracted_text": "Page one text",
            "page_start": 1,
            "page_end": 2,
        }
    )

    assert out["pdf_read"] is True
    assert out["source_kind"] == "pdf"
    assert out["status"] == "verified"
    assert out["verified_text"] is True
    assert out["page_citations"] == [
        {"page": 1, "url": "https://example.com/paper.pdf"},
        {"page": 2, "url": "https://example.com/paper.pdf"},
    ]


@pytest.mark.asyncio
async def test_pdf_read_rejects_invalid_pdf_magic() -> None:
    """pdf_read should not present non-PDF bytes as verified evidence."""
    registry = ToolRegistry()
    register_web_tools(registry)
    tool = registry.get("pdf_read")
    assert tool is not None

    out = await tool.handler(
        {
            "url": "https://example.com/not-pdf.pdf",
            "mock_pdf_bytes": "not a pdf",
        }
    )

    assert out["pdf_read"] is True
    assert out["status"] == "failed"
    assert out["verified_text"] is False
    assert out["error"] == "invalid_pdf"


@pytest.mark.asyncio
async def test_pdf_read_reports_too_large_mock_pdf() -> None:
    """pdf_read should report oversized PDFs before verification."""
    registry = ToolRegistry()
    register_web_tools(registry)
    tool = registry.get("pdf_read")
    assert tool is not None

    out = await tool.handler(
        {
            "url": "https://example.com/large.pdf",
            "mock_pdf_bytes": "%PDF-1.4\n" + ("x" * 300),
            "max_bytes": 256,
        }
    )

    assert out["verified_text"] is False
    assert out["error"] == "pdf_too_large"


@pytest.mark.asyncio
async def test_browser_read_is_read_only_fetch_fallback() -> None:
    """browser_read should remain read-only and avoid browser actions."""
    registry = ToolRegistry()
    register_web_tools(registry)
    tool = registry.get("browser_read")
    assert tool is not None

    out = await tool.handler(
        {
            "url": "https://example.com/page",
            "mock_status_code": 200,
            "mock_content": "<h1>Hello</h1>",
            "mock_content_type": "text/html",
        }
    )

    assert out["browser_read"] is True
    assert out["source_kind"] == "rendered_page"
    assert out["status"] == "verified"
    assert out["fallback_reason"] == "source_read_or_pdf_read_insufficient"
    assert out["browser_action_allowed"] is False
    assert out["rendered"] is False
    assert "Hello" in out["content"]


@pytest.mark.asyncio
async def test_browser_read_blocks_private_host_even_with_override() -> None:
    """browser_read fallback should not allow private-network SSRF overrides."""
    registry = ToolRegistry()
    register_web_tools(registry)
    tool = registry.get("browser_read")
    assert tool is not None

    with pytest.raises(ValueError, match="private/localhost hosts are blocked"):
        await tool.handler(
            {
                "url": "http://127.0.0.1/private",
                "allow_private_host": True,
                "mock_status_code": 200,
                "mock_content": "secret",
            }
        )


@pytest.mark.asyncio
async def test_browser_read_blocks_cloud_metadata_endpoint() -> None:
    """browser_read should reject link-local cloud metadata addresses."""
    registry = ToolRegistry()
    register_web_tools(registry)
    tool = registry.get("browser_read")
    assert tool is not None

    with pytest.raises(ValueError, match="private/localhost hosts are blocked"):
        await tool.handler(
            {
                "url": "http://169.254.169.254/latest/meta-data",
                "mock_status_code": 200,
                "mock_content": "metadata",
            }
        )


@pytest.mark.asyncio
async def test_web_fetch_rejects_binary_content_type(monkeypatch) -> None:
    """web_fetch should reject non-text response types."""
    response = _DummyResponse(
        url="https://example.com/file.bin",
        content=b"\x00\x01\x02",
        headers={"content-type": "application/octet-stream"},
    )

    def _client_factory(*_args, **_kwargs):
        return _DummyClient(response)

    monkeypatch.setattr(
        "agent_driver.tools.builtin.web.httpx.AsyncClient", _client_factory
    )
    registry = ToolRegistry()
    register_web_tools(registry)
    tool = registry.get("web_fetch")
    assert tool is not None
    with pytest.raises(ValueError, match="unsupported content type"):
        await tool.handler({"url": "https://example.com/file.bin"})


@pytest.mark.asyncio
async def test_web_search_uses_mock_results_without_network() -> None:
    """web_search should support offline mock results mode."""
    registry = ToolRegistry()
    register_web_tools(registry)
    tool = registry.get("web_search")
    assert tool is not None
    out = await tool.handler(
        {
            "query": "agent runtime",
            "mock_results": [
                {"title": "Doc A", "url": "https://a.test", "snippet": "A"},
                {"title": "Doc B", "url": "https://b.test", "snippet": "B"},
            ],
        }
    )
    assert out["source"] == "mock"
    assert len(out["results"]) == 2
    assert out["results"][0]["title"] == "Doc A"
    assert out["truncated"] is False
    assert out["parse_status"] == "ok"


@pytest.mark.asyncio
async def test_web_search_sets_truncated_when_mock_results_hit_cap() -> None:
    """web_search should mark truncated when max_results cap is reached."""
    registry = ToolRegistry()
    register_web_tools(registry)
    tool = registry.get("web_search")
    assert tool is not None
    out = await tool.handler(
        {
            "query": "agent runtime",
            "max_results": 1,
            "mock_results": [
                {"title": "Doc A", "url": "https://a.test", "snippet": "A"},
                {"title": "Doc B", "url": "https://b.test", "snippet": "B"},
            ],
        }
    )
    assert out["returned_count"] == 1
    assert out["truncated"] is True
    assert out["max_results"] == 1


@pytest.mark.asyncio
async def test_web_fetch_rejects_non_http_scheme() -> None:
    """web_fetch should validate scheme before any network call."""
    registry = ToolRegistry()
    register_web_tools(registry)
    tool = registry.get("web_fetch")
    assert tool is not None
    with pytest.raises(ValueError, match="scheme"):
        await tool.handler({"url": "ftp://example.com/file.txt"})


@pytest.mark.asyncio
async def test_web_fetch_rejects_private_host_by_default() -> None:
    """web_fetch should reject localhost/private host without explicit override."""
    registry = ToolRegistry()
    register_web_tools(registry)
    tool = registry.get("web_fetch")
    assert tool is not None
    with pytest.raises(ValueError, match="private/localhost"):
        await tool.handler({"url": "http://127.0.0.1:8080/health"})


@pytest.mark.asyncio
async def test_web_fetch_truncates_when_response_exceeds_max_bytes(monkeypatch) -> None:
    """web_fetch should mark truncated when response is byte-capped."""
    response = _DummyResponse(
        url="https://example.com",
        content=("abcdefghij" * 10).encode("utf-8"),
        headers={"content-type": "text/plain"},
    )

    def _client_factory(*_args, **_kwargs):
        return _DummyClient(response)

    monkeypatch.setattr(
        "agent_driver.tools.builtin.web.httpx.AsyncClient", _client_factory
    )
    registry = ToolRegistry()
    register_web_tools(registry)
    tool = registry.get("web_fetch")
    assert tool is not None
    out = await tool.handler({"url": "https://example.com", "max_chars": 64})
    assert len(out["content"]) == 64
    assert out["truncated"] is True


@pytest.mark.asyncio
async def test_web_fetch_extract_mode_text_for_html(monkeypatch) -> None:
    """web_fetch should strip tags in text extraction mode."""
    html = "<html><body><h1>Title</h1><p>Paragraph</p></body></html>"
    response = _DummyResponse(
        url="https://example.com",
        content=html.encode("utf-8"),
        headers={"content-type": "text/html; charset=utf-8"},
    )

    def _client_factory(*_args, **_kwargs):
        return _DummyClient(response)

    monkeypatch.setattr(
        "agent_driver.tools.builtin.web.httpx.AsyncClient", _client_factory
    )
    registry = ToolRegistry()
    register_web_tools(registry)
    tool = registry.get("web_fetch")
    assert tool is not None
    out = await tool.handler(
        {"url": "https://example.com", "extract_mode": "text", "max_chars": 200}
    )
    assert "Title Paragraph" == out["content"]
    assert out["extract_mode"] == "text"


@pytest.mark.asyncio
async def test_web_fetch_extracts_og_metadata_and_strips_script_style(
    monkeypatch,
) -> None:
    """web_fetch should preserve OG metadata and remove embedded script/style content."""
    html = (
        "<html><head>"
        '<meta property="og:title" content="Segment Anything 3" />'
        '<meta name="og:description" content="Latest release notes" />'
        '<meta property="og:url" content="https://example.com/sam3" />'
        '<meta property="article:published_time" content="2025-04-10" />'
        "<style>.hidden {display:none;}</style>"
        "</head><body>"
        "<script>console.log('noise');</script>"
        "<p>Model details</p>"
        "</body></html>"
    )
    response = _DummyResponse(
        url="https://example.com",
        content=html.encode("utf-8"),
        headers={"content-type": "text/html; charset=utf-8"},
    )

    def _client_factory(*_args, **_kwargs):
        return _DummyClient(response)

    monkeypatch.setattr(
        "agent_driver.tools.builtin.web.httpx.AsyncClient", _client_factory
    )
    registry = ToolRegistry()
    register_web_tools(registry)
    tool = registry.get("web_fetch")
    assert tool is not None
    out = await tool.handler(
        {"url": "https://example.com", "extract_mode": "markdown", "max_chars": 500}
    )
    assert out["metadata"] == {
        "title": "Segment Anything 3",
        "description": "Latest release notes",
        "url": "https://example.com/sam3",
        "published_time": "2025-04-10",
    }
    assert out["excerpt"] == out["content"]
    assert "Title: Segment Anything 3" in out["content"]
    assert "Description: Latest release notes" in out["content"]
    assert "Model details" in out["content"]
    assert "console.log('noise')" not in out["content"]
    assert ".hidden {display:none;}" not in out["content"]
    assert isinstance(out.get("excerpt"), str)
    assert out["max_chars_applied"] == 500


@pytest.mark.asyncio
async def test_web_fetch_caps_requested_max_chars(monkeypatch) -> None:
    """web_fetch should cap oversized max_chars requests server-side."""
    response = _DummyResponse(
        url="https://example.com",
        content=b"abcdefghij",
        headers={"content-type": "text/plain"},
    )

    def _client_factory(*_args, **_kwargs):
        return _DummyClient(response)

    monkeypatch.setattr(
        "agent_driver.tools.builtin.web.httpx.AsyncClient", _client_factory
    )
    registry = ToolRegistry()
    register_web_tools(registry)
    tool = registry.get("web_fetch")
    assert tool is not None
    out = await tool.handler({"url": "https://example.com", "max_chars": 50000})
    assert out["max_chars_applied"] == 8000
    assert len(out["content"]) == 10


@pytest.mark.asyncio
async def test_web_fetch_wraps_http_errors(monkeypatch) -> None:
    """web_fetch should wrap HTTP exceptions with stable error prefix."""
    response = _DummyResponse(
        url="https://example.com/fail",
        status_code=500,
        content=b"fail",
        headers={"content-type": "text/plain"},
    )

    def _client_factory(*_args, **_kwargs):
        return _DummyClient(response)

    monkeypatch.setattr(
        "agent_driver.tools.builtin.web.httpx.AsyncClient", _client_factory
    )
    registry = ToolRegistry()
    register_web_tools(registry)
    tool = registry.get("web_fetch")
    assert tool is not None
    with pytest.raises(ValueError, match="web_fetch failed"):
        await tool.handler({"url": "https://example.com/fail"})


@pytest.mark.asyncio
async def test_web_fetch_returns_blocked_payload_for_forbidden_text(
    monkeypatch,
) -> None:
    """403 text pages should guide the model to try another source, not hard-fail."""
    response = _DummyResponse(
        url="https://example.com/blocked",
        status_code=403,
        content=b"<html><head><title>Blocked</title></head><body>Forbidden</body></html>",
        headers={"content-type": "text/html"},
    )

    def _client_factory(*_args, **_kwargs):
        return _DummyClient(response)

    monkeypatch.setattr(
        "agent_driver.tools.builtin.web.httpx.AsyncClient",
        _client_factory,
    )
    registry = ToolRegistry()
    register_web_tools(registry)
    tool = registry.get("web_fetch")
    assert tool is not None
    out = await tool.handler({"url": "https://example.com/blocked"})
    assert out["status_code"] == 403
    assert out["blocked"] is True
    assert out["content"] == ""
    assert "try another search result" in out["summary"]


@pytest.mark.asyncio
async def test_web_fetch_supports_mock_blocked_payload() -> None:
    """mock_status_code should let deterministic probes exercise blocked fetches."""
    registry = ToolRegistry()
    register_web_tools(registry)
    tool = registry.get("web_fetch")
    assert tool is not None
    out = await tool.handler(
        {
            "url": "https://example.com/blocked",
            "mock_status_code": 403,
            "mock_content": (
                "<html><head>"
                '<meta property="og:title" content="Blocked" />'
                "</head></html>"
            ),
            "mock_content_type": "text/html",
        }
    )
    assert out["status_code"] == 403
    assert out["blocked"] is True
    assert out["content"] == ""
    assert out["metadata"]["title"] == "Blocked"


@pytest.mark.asyncio
async def test_web_fetch_returns_unavailable_payload_after_timeouts(
    monkeypatch,
) -> None:
    """Persistent fetch timeouts should guide the model to another source."""

    class _TimeoutClient:
        async def __aenter__(self) -> "_TimeoutClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        async def get(
            self, url: str, headers: dict[str, str] | None = None
        ) -> _DummyResponse:
            _ = (url, headers)
            raise httpx.ConnectTimeout("connect timed out")

    def _client_factory(*_args, **_kwargs):
        return _TimeoutClient()

    monkeypatch.setattr(
        "agent_driver.tools.builtin.web.httpx.AsyncClient",
        _client_factory,
    )
    registry = ToolRegistry()
    register_web_tools(registry)
    tool = registry.get("web_fetch")
    assert tool is not None
    out = await tool.handler({"url": "https://example.com/slow"})
    assert out["status_code"] is None
    assert out["unavailable"] is True
    assert out["content"] == ""
    assert "try another search result" in out["summary"]


@pytest.mark.asyncio
async def test_web_search_parses_duckduckgo_html(monkeypatch) -> None:
    """web_search should parse at least one result from DDG-like HTML."""
    html = (
        "<html><body>"
        '<a class="result-link" href="https://example.com/page">Example Title</a>'
        "</body></html>"
    )
    response = _DummyResponse(
        url="https://duckduckgo.com/html/?q=test",
        content=html.encode("utf-8"),
        headers={"content-type": "text/html"},
    )

    def _client_factory(*_args, **_kwargs):
        return _DummyClient(response)

    monkeypatch.setattr(
        "agent_driver.tools.builtin.web.httpx.AsyncClient", _client_factory
    )
    registry = ToolRegistry()
    register_web_tools(registry)
    tool = registry.get("web_search")
    assert tool is not None
    out = await tool.handler({"query": "test", "max_results": 1})
    assert out["source"] == "duckduckgo_html"
    assert len(out["results"]) == 1
    assert out["results"][0]["url"] == "https://example.com/page"
    assert out["result_preview_urls"] == ["https://example.com/page"]
    assert out["truncated"] is True
    assert out["max_results"] == 1


@pytest.mark.asyncio
async def test_web_search_parses_alternate_duckduckgo_anchor_class(monkeypatch) -> None:
    """web_search should support result__a anchor fallback parsing."""
    html = (
        "<html><body>"
        '<a class="result__a" href="https://example.com/alt">Alt Title</a>'
        "</body></html>"
    )
    response = _DummyResponse(
        url="https://duckduckgo.com/html/?q=test",
        content=html.encode("utf-8"),
        headers={"content-type": "text/html"},
    )

    def _client_factory(*_args, **_kwargs):
        return _DummyClient(response)

    monkeypatch.setattr(
        "agent_driver.tools.builtin.web.httpx.AsyncClient", _client_factory
    )
    registry = ToolRegistry()
    register_web_tools(registry)
    tool = registry.get("web_search")
    assert tool is not None
    out = await tool.handler({"query": "test", "max_results": 1})
    assert len(out["results"]) == 1
    assert out["results"][0]["url"] == "https://example.com/alt"


@pytest.mark.asyncio
async def test_web_search_unwraps_duckduckgo_redirect_url(monkeypatch) -> None:
    """web_search should normalize DDG redirect href into target URL."""
    html = (
        "<html><body>"
        '<a class="result-link" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Ftarget">Target</a>'
        "</body></html>"
    )
    response = _DummyResponse(
        url="https://duckduckgo.com/html/?q=test",
        content=html.encode("utf-8"),
        headers={"content-type": "text/html"},
    )

    def _client_factory(*_args, **_kwargs):
        return _DummyClient(response)

    monkeypatch.setattr(
        "agent_driver.tools.builtin.web.httpx.AsyncClient", _client_factory
    )
    registry = ToolRegistry()
    register_web_tools(registry)
    tool = registry.get("web_search")
    assert tool is not None
    out = await tool.handler({"query": "test", "max_results": 1})
    assert out["results"][0]["url"] == "https://example.com/target"
    assert out["result_preview_urls"] == ["https://example.com/target"]


@pytest.mark.asyncio
async def test_web_search_relaxes_site_operator_when_ddg_returns_no_links(
    monkeypatch,
) -> None:
    """site: queries often yield empty DDG html; server should retry without site:."""
    empty_html = "<html><body><div>no links</div></body></html>"
    results_html = '<a class="result-link" href="https://ai.meta.com/sam3/">SAM 3</a>'
    calls: list[str] = []

    def _client_factory(*_args, **_kwargs):
        class _Client:
            async def get(self, url, headers=None):
                calls.append(url)
                body = results_html if len(calls) > 1 else empty_html
                return _DummyResponse(
                    url=url,
                    content=body.encode("utf-8"),
                    headers={"content-type": "text/html"},
                )

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return None

        return _Client()

    monkeypatch.setattr(
        "agent_driver.tools.builtin.web.httpx.AsyncClient", _client_factory
    )
    registry = ToolRegistry()
    register_web_tools(registry)
    tool = registry.get("web_search")
    assert tool is not None
    out = await tool.handler(
        {
            "query": "latest Segment Anything release site:meta.ai",
            "max_results": 3,
        }
    )
    assert out["returned_count"] == 1
    assert out["query_relaxation"] == "stripped_site_operator"
    assert "meta.ai" in out["query"]
    assert out["results"][0]["url"] == "https://ai.meta.com/sam3/"


@pytest.mark.asyncio
async def test_web_search_includes_diagnostic_when_no_results_parsed(
    monkeypatch,
) -> None:
    """web_search should provide diagnostic metadata for empty parsed results."""
    html = "<html><body><div>no links</div></body></html>"
    response = _DummyResponse(
        url="https://duckduckgo.com/html/?q=test",
        content=html.encode("utf-8"),
        headers={"content-type": "text/html"},
    )

    def _client_factory(*_args, **_kwargs):
        return _DummyClient(response)

    monkeypatch.setattr(
        "agent_driver.tools.builtin.web.httpx.AsyncClient", _client_factory
    )
    registry = ToolRegistry()
    register_web_tools(registry)
    tool = registry.get("web_search")
    assert tool is not None
    out = await tool.handler({"query": "test", "max_results": 2})
    assert out["results"] == []
    diagnostic = out.get("diagnostic")
    assert isinstance(diagnostic, dict)
    assert diagnostic.get("status") == "no_results_parsed"
    assert out["parse_status"] == "parse_failed"


@pytest.mark.asyncio
async def test_web_search_returns_upstream_error_payload_on_provider_failure(
    monkeypatch,
) -> None:
    """web_search should return graceful empty payload when upstream fails."""
    response = _DummyResponse(
        url="https://duckduckgo.com/html/?q=test",
        status_code=503,
        content=b"unavailable",
        headers={"content-type": "text/html"},
    )

    def _client_factory(*_args, **_kwargs):
        return _DummyClient(response)

    monkeypatch.setattr(
        "agent_driver.tools.builtin.web.httpx.AsyncClient", _client_factory
    )
    registry = ToolRegistry()
    register_web_tools(registry)
    tool = registry.get("web_search")
    assert tool is not None
    out = await tool.handler({"query": "test", "max_results": 2})
    assert out["results"] == []
    assert out["parse_status"] == "upstream_error"
    assert "web_search unavailable" in out["summary"]


@pytest.mark.asyncio
async def test_web_search_retries_connect_timeout_and_uses_html_endpoint(
    monkeypatch,
) -> None:
    """DDG search should retry transient connect timeouts against the HTML endpoint."""
    html = '<html><body><a class="result__a" href="https://example.com">Hit</a></body></html>'
    response = _DummyResponse(
        url="https://html.duckduckgo.com/html/?q=test",
        content=html.encode("utf-8"),
        headers={"content-type": "text/html"},
    )
    calls: list[str] = []

    class _FlakyClient:
        async def __aenter__(self) -> "_FlakyClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            return None

        async def get(
            self, url: str, headers: dict[str, str] | None = None
        ) -> _DummyResponse:
            _ = headers
            calls.append(url)
            if len(calls) == 1:
                raise httpx.ConnectTimeout("connect timed out")
            return response

    def _client_factory(*_args, **_kwargs):
        return _FlakyClient()

    monkeypatch.setattr(
        "agent_driver.tools.builtin.web.httpx.AsyncClient",
        _client_factory,
    )
    registry = ToolRegistry()
    register_web_tools(registry)
    tool = registry.get("web_search")
    assert tool is not None
    out = await tool.handler({"query": "test", "max_results": 2})
    assert len(calls) == 2
    assert calls[0].startswith("https://html.duckduckgo.com/html/?")
    assert out["parse_status"] == "ok"
    assert out["results"][0]["title"] == "Hit"
