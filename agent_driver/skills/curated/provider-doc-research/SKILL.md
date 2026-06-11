---
name: provider-doc-research
description: Research provider or API behavior using official documentation first.
when_to_use: Use for SDK, API, model, pricing, limits, or provider behavior questions.
tags: [research, provider-docs, official-sources]
allowed_tools: [web_search, web_fetch]
context:
  official_sources_first: true
source: bundled
---
# Provider Documentation Research

Start with official provider documentation, changelogs, status pages, and
repository docs. Use secondary sources only to find official URLs or explain
ecosystem impact. Label inferred behavior separately from documented behavior.

When a claim is version-sensitive, include the concrete doc URL and the date or
version when present.
