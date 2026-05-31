---
name: deep-research-report
description: Plan, search, fetch, synthesize and cite a source-verified report.
when_to_use: Use for report-like research where search candidates must be verified before synthesis.
tags: [research, deep-research, report, citations]
allowed_tools: [web_search, web_fetch, todo_write, run_subagent]
context:
  depth: deep_parallel_research
source: bundled
---
# Deep Research Report

Use this workflow for report-style research:

1. Restate the research question and likely subtopics.
2. Search broadly enough to identify credible candidate sources.
3. Fetch concrete URLs before treating claims as verified evidence.
4. Track failed or blocked reads separately from verified reads.
5. Synthesize from verified reads only; search snippets can suggest leads but
   must not become final evidence on their own.
6. End with concise citations to the URLs used.

If subagents are available, delegate source discovery or narrow subtopics. The
parent agent keeps final synthesis and citation coverage.
