# Deep Research And Skills Decision Record

Status: reference / compacted. Unified Work Plan phases 4, 5 and 5A closed the
Skills and provider-neutral Deep Research contract work. Active continuation is
now tracked in [Unified Work Plan](unified-work-plan-2026-05-31.md) and
[Efficient Deep Research Workspace Architecture](efficient-deep-research-workspace-architecture-2026-05-31.md).

Date: 2026-05-31.

## Decision

Skills and Deep Research are complementary:

- Skills provide just-in-time procedural context through explicit discovery and
  `skill_view`.
- Deep Research remains a runtime contract: plan/progress, source ledger,
  verified reads, optional subagents, final-readiness checks and citations.
- Chat-demo may expose the UX, but parsing skills, trust policy, evidence
  ledger and final-readiness logic belong in `agent_driver`.
- Provider-native Deep Research can feed the same source ledger later, but it
  must not become the only implementation.

## Closed Work

- `agent_driver.skills` with metadata parsing, trust classification,
  supporting-file index and curated research skills.
- `skill_tool` metadata listing and `skill_view` on-demand body/file loading.
- Skill invocation records in runtime events/metadata and compaction
  projection.
- Source ledger rows for search candidates, verified reads, blocked reads and
  assistant links.
- `deep_parallel_research` as a provider-neutral depth contract.
- Trusted-only skill preload for subagents.
- Chat-demo Deep mode, skills panel, source-ledger rendering and deterministic
  fake scenarios.

## Active Continuation

The remaining Deep Research issue is not skill discovery. It is output
efficiency:

- long report drafts should become session artifacts, not chat-only text;
- todo repair should patch/update the artifact instead of forcing a full report
  rewrite;
- source ledgers and large tool outputs should be durable workspace files;
- compaction should project artifact/source refs, not full bodies.

That work is tracked by the active unified plan phases for artifact-first
Deep Research.

## Reference Sources

The original analysis used these external reference points:

- OpenAI Deep Research and web-search docs:
  <https://developers.openai.com/api/docs/guides/deep-research>,
  <https://developers.openai.com/api/docs/guides/tools-web-search>
- OpenAI Skills:
  <https://help.openai.com/en/articles/20001066-skills-in-chatgpt>
- Anthropic Agent Skills:
  <https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills>
- Anthropic multi-agent research system:
  <https://www.anthropic.com/engineering/built-multi-agent-research-system>

Keep this page as the product/architecture rationale. Do not add new active
checkboxes here; copy actionable work into the unified plan.
