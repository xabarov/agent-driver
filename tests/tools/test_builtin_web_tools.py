"""Tests for built-in web fetch and search tools."""

# pylint: disable=too-few-public-methods

from __future__ import annotations

import pytest

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

    async def get(self, url: str, headers: dict[str, str] | None = None) -> _DummyResponse:
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

    monkeypatch.setattr("agent_driver.tools.builtin.web.httpx.AsyncClient", _client_factory)
    registry = ToolRegistry()
    register_web_tools(registry)
    tool = registry.get("web_fetch")
    assert tool is not None
    out = await tool.handler({"url": "https://example.com"})
    assert out["status_code"] == 200
    assert out["content"] == "hello web tool"
    assert out["truncated"] is False


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

    monkeypatch.setattr("agent_driver.tools.builtin.web.httpx.AsyncClient", _client_factory)
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
