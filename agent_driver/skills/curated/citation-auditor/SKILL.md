---
name: citation-auditor
description: Check that final claims are backed by fetched source URLs.
when_to_use: Use before finalizing source-based answers or reports.
tags: [research, citations, audit]
allowed_tools: [web_fetch]
context:
  audit: citation_coverage
source: bundled
---
# Citation Auditor

Before final answer:

1. List the concrete URLs that were fetched or otherwise verified.
2. Check whether key claims in the answer trace back to those URLs.
3. Remove or qualify claims that are supported only by search candidates.
4. Ensure the final answer contains clickable Markdown links.
