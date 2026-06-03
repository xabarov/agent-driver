---
name: deep-research-report
description: Plan, search, fetch, synthesize and cite a source-verified report.
when_to_use: Use for report-like research where search candidates must be verified before synthesis.
tags: [research, deep-research, report, citations]
allowed_tools: [todo_write, web_search, web_fetch, source_read, agent_tool, read_file, file_write, file_edit, file_patch, artifact_list, artifact_read, artifact_preview]
context:
  depth: deep_parallel_research
source: bundled
---
# Deep Research Report

Use this workflow for report-style research. Durable report content belongs in
`research/report.md`; chat should stay concise.

## Profiles

### Light

Use light only for source-backed short answers. Search and fetch concrete URLs,
then answer briefly in chat. Do not create report artifacts and do not delegate
to subagents.

### Medium

Use medium for artifact-first reports. Create a visible todo plan, run at most
one bounded `agent_tool` child for independent source discovery, verify concrete
URLs with `web_fetch`, then have the parent write `research/report.md` and
`research/sources.jsonl`.

### Hard

Use hard for high-value research that needs audit/export. Start from the medium
contract, then add verifier/auditor work, claim-source checking, broader source
coverage, and export steps only when the hard profile explicitly allows them.

## Workflow

1. Create or update a visible todo plan.
2. Identify independent subtopics or source families. If `agent_tool` is
   available and the subtopics are independent, delegate bounded source
   discovery tasks; the parent keeps final synthesis.
3. Search broadly enough to identify credible candidate sources.
4. Fetch concrete URLs before treating claims as verified evidence.
5. Track failed or blocked reads separately from verified reads.
6. Create `research/report.md` with `file_write` before writing a long
   synthesis. For later changes, read or preview the artifact and use
   `file_edit` or `file_patch` instead of rewriting the full report.
7. Synthesize from verified reads only; search snippets can suggest leads but
   must not become final evidence on their own.
8. End with a short chat handoff that references `research/report.md` and the
   fetched URLs or source ledger.

Subagents should return compact notes with URLs, fetched/blocked status, and
open questions. They should not write the parent report unless explicitly asked
by the parent.
