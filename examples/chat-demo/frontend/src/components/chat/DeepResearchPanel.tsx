import { BookOpenCheck, CircleAlert, FileText, FileSearch, Radio } from "lucide-react";

import type { DeepResearchState } from "../../lib/events";
import { Badge } from "../ui/badge";

interface DeepResearchPanelProps {
  state?: DeepResearchState;
}

export function DeepResearchPanel({ state }: DeepResearchPanelProps) {
  const ledger = state?.ledger;
  const artifact = state?.artifact;
  if (!ledger && !artifact && !state?.progress.length) {
    return null;
  }
  const verified = ledger?.verifiedReads.length ?? 0;
  const candidates = ledger?.searchCandidates.length ?? 0;
  const failed = (ledger?.failedReads.length ?? 0) + (ledger?.blockedReads.length ?? 0);
  const domains = new Set(
    (ledger?.verifiedReads ?? [])
      .map((source) => source.domain)
      .filter((domain): domain is string => Boolean(domain)),
  ).size;

  return (
    <section
      aria-label="Deep research diagnostics"
      className="mt-3 rounded-md border border-border/70 bg-background/60 p-3"
    >
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="inline-flex items-center gap-1.5 font-medium">
          <BookOpenCheck className="h-3.5 w-3.5" aria-hidden />
          Deep Research
        </span>
        <Badge variant="outline">{verified} verified</Badge>
        <Badge variant="outline">{candidates} candidates</Badge>
        {domains ? <Badge variant="outline">{domains} domains</Badge> : null}
        {artifact ? <Badge variant="outline">{artifact.reportPath}</Badge> : null}
        {failed ? (
          <Badge variant="secondary" className="bg-amber-500/15 text-amber-700">
            {failed} blocked
          </Badge>
        ) : null}
      </div>
      {artifact ? (
        <div className="mt-2 flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
          <FileText className="h-3.5 w-3.5 shrink-0 text-emerald-600" aria-hidden />
          <span className="truncate">{artifact.reportPath}</span>
          {artifact.reportSizeBytes ? (
            <span className="shrink-0 tabular-nums">
              {formatBytes(artifact.reportSizeBytes)}
            </span>
          ) : null}
        </div>
      ) : null}
      {state?.progress.length ? (
        <ol className="mt-2 grid gap-1.5 text-xs text-muted-foreground">
          {state.progress.slice(-4).map((item) => (
            <li key={`${item.seq}:${item.event}`} className="flex items-center gap-2">
              {item.event === "source_ledger_updated" ? (
                <FileSearch className="h-3.5 w-3.5 text-sky-600" aria-hidden />
              ) : item.event === "citation_coverage_updated" ? (
                <CircleAlert className="h-3.5 w-3.5 text-amber-600" aria-hidden />
              ) : (
                <Radio className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
              )}
              <span className="truncate">{item.label}</span>
            </li>
          ))}
        </ol>
      ) : null}
    </section>
  );
}

function formatBytes(value: number): string {
  if (value >= 1024 * 1024) {
    return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  }
  if (value >= 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${Math.round(value)} B`;
}
