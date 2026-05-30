from agent_driver.observability.source_evidence import (
    merge_source_evidence,
    source_evidence_from_tool_result,
)


def test_extracts_web_fetch_source_evidence():
    sources = source_evidence_from_tool_result(
        tool_name="web_fetch",
        tool_call_id="call_1",
        structured_output={
            "url": "https://Example.com/article#section",
            "metadata": {"title": "Fetched Article", "published_time": "2026-05-30"},
            "excerpt": "Useful page excerpt.",
        },
    )

    assert sources == [
        {
            "id": "web_fetch:call_1:1",
            "url": "https://Example.com/article#section",
            "canonical_url": "https://example.com/article",
            "domain": "example.com",
            "source_type": "web_fetch",
            "title": "Fetched Article",
            "excerpt": "Useful page excerpt.",
            "published_at": "2026-05-30",
            "tool_call_id": "call_1",
            "rank": 1,
        }
    ]


def test_failed_web_fetch_does_not_become_source_evidence():
    sources = source_evidence_from_tool_result(
        tool_name="web_fetch",
        tool_call_id="call_1",
        structured_output={
            "url": "https://example.com/blocked",
            "status": "failed",
            "error": "HTTP 403",
            "remediation": "try another source",
        },
    )

    assert sources == []


def test_blocked_http_web_fetch_does_not_become_source_evidence():
    sources = source_evidence_from_tool_result(
        tool_name="web_fetch",
        tool_call_id="call_1",
        structured_output={
            "url": "https://example.com/blocked",
            "status_code": 403,
            "remediation": "try another source",
        },
    )

    assert sources == []


def test_extracts_web_search_source_evidence():
    sources = source_evidence_from_tool_result(
        tool_name="web_search",
        tool_call_id="call_2",
        structured_output={
            "results": [
                {
                    "title": "Result One",
                    "url": "https://example.com/one",
                    "snippet": "First snippet",
                },
                {"title": "Invalid", "url": "mailto:test@example.com"},
            ]
        },
    )

    assert sources == [
        {
            "id": "web_search:call_2:1",
            "url": "https://example.com/one",
            "canonical_url": "https://example.com/one",
            "domain": "example.com",
            "source_type": "web_search",
            "title": "Result One",
            "excerpt": "First snippet",
            "tool_call_id": "call_2",
            "rank": 1,
        }
    ]


def test_merge_source_evidence_prefers_fetched_pages():
    merged = merge_source_evidence(
        [
            {
                "url": "https://example.com/a",
                "canonical_url": "https://example.com/a",
                "source_type": "web_search",
                "title": "Search title",
                "rank": 1,
            },
            {
                "url": "https://example.com/a",
                "canonical_url": "https://example.com/a",
                "source_type": "web_fetch",
                "excerpt": "Fetched excerpt",
                "rank": 2,
            },
        ]
    )

    assert merged == [
        {
            "url": "https://example.com/a",
            "canonical_url": "https://example.com/a",
            "source_type": "web_fetch",
            "title": "Search title",
            "excerpt": "Fetched excerpt",
            "rank": 2,
        }
    ]
