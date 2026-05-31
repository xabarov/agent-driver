# Context Pressure Planlet

Status: active input for
[Unified Work Plan Phase 2](unified-work-plan-2026-05-31.md#phase-2---harness-context-pressure).

Date: 2026-05-31.

## Observation

On large-context coding/research runs, quality appears much better while the
agent stays inside roughly the first 40% of the available context. Past that
point the run enters a "dumb zone": the model can still continue, but it is
more likely to lose source discipline, skip verification, repeat itself or
finish with progress-only prose.

The existing 92% hard compaction threshold is still useful as an emergency
guard, but it is too late to be the first intervention.

External context note:

- HumanLayer's "Advanced Context Engineering for Coding Agents" recommends
  intentional compaction and subagents as context-control tools:
  <https://github.com/humanlayer/advanced-context-engineering-for-coding-agents/blob/main/ace-fca.md>

## Working Hypothesis

Context pressure should become a graded runtime signal, not only a final
compaction trigger. The agent should know the current context usage ratio and
receive earlier nudges to summarize, delegate read-heavy work, preserve source
references or move to synthesis before the run degrades.

## Implementation Notes

- Add `context_usage_ratio` to token pressure snapshots.
- Introduce graded states:
  `ok`, `early_warning`, `delegate_or_summarize`,
  `compact_recommended`, `blocking`.
- Start model-facing guidance around 35-45% context usage.
- Keep the current high-water emergency behavior near 92%.
- Emit trace/stream diagnostics when the pressure state changes.
- Record whether the run followed or ignored a recommendation.

## Acceptance Criteria

- Long research/code tasks show early pressure diagnostics in traces before
  emergency compaction.
- The model receives actionable guidance before context pressure becomes
  terminal.
- Trace summary can explain context-pressure outcome:
  recommendation emitted, recommendation followed/ignored, compaction
  recommended, compaction executed or blocking reached.
- Phase 2 evals include at least one long research/code scenario that compares
  behavior before and after early pressure nudges.
