from __future__ import annotations

from agent_driver.runtime.research_evidence import (
    research_source_ledger_from_tool_results,
)


def test_source_ledger_rows_include_explicit_status() -> None:
    ledger = research_source_ledger_from_tool_results(
        [
            {
                "call": {
                    "tool_name": "web_search",
                    "tool_call_id": "search_1",
                    "args": {"query": "fork join"},
                },
                "structured_output": {
                    "results": [
                        {
                            "title": "Candidate",
                            "url": "https://example.com/candidate",
                            "snippet": "Candidate source",
                        }
                    ]
                },
            },
            {
                "call": {
                    "tool_name": "web_fetch",
                    "tool_call_id": "fetch_1",
                    "args": {"url": "https://example.com/verified"},
                },
                "structured_output": {"url": "https://example.com/verified"},
            },
            {
                "call": {
                    "tool_name": "web_fetch",
                    "tool_call_id": "fetch_2",
                    "args": {"url": "https://example.com/blocked"},
                },
                "structured_output": {
                    "url": "https://example.com/blocked",
                    "status_code": 403,
                },
            },
            {
                "call": {
                    "tool_name": "web_fetch",
                    "tool_call_id": "fetch_3",
                    "args": {"url": "https://example.com/failed"},
                },
                "structured_output": {
                    "url": "https://example.com/failed",
                    "status": "error",
                },
            },
        ]
    )

    assert ledger.search_candidates[0]["status"] == "candidate"
    assert ledger.verified_reads[0]["status"] == "verified"
    assert ledger.blocked_reads[0]["status"] == "blocked"
    assert ledger.failed_reads[0]["status"] == "failed"
