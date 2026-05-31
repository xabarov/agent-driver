import { ExternalLink, Link2 } from "lucide-react";

import type { SourceEvidence } from "../../lib/sourceEvidence";
import { Badge } from "../ui/badge";

interface CitationShelfProps {
  sources: SourceEvidence[];
}

function sourceLabel(source: SourceEvidence, index: number): string {
  return source.title || source.domain || `Source ${index + 1}`;
}

function sourceBadge(source: SourceEvidence): string {
  if (source.sourceType === "web_fetch") {
    return "fetched";
  }
  if (source.sourceType === "web_search") {
    return "candidate";
  }
  return "linked";
}

function hasVerifiedSource(sources: SourceEvidence[]): boolean {
  return sources.some((source) => source.sourceType !== "web_search");
}

function coverageLabel(sources: SourceEvidence[]): string {
  const fetched = sources.filter((source) => source.sourceType === "web_fetch").length;
  const search = sources.filter((source) => source.sourceType === "web_search").length;
  const linked = sources.filter(
    (source) => source.sourceType === "assistant_link",
  ).length;
  const domains = new Set(
    sources
      .map((source) => source.domain)
      .filter((domain): domain is string => Boolean(domain)),
  ).size;
  const parts = [
    fetched ? `${fetched} fetched` : undefined,
    search ? `${search} search` : undefined,
    linked ? `${linked} linked` : undefined,
    domains ? `${domains} domains` : undefined,
  ].filter((item): item is string => Boolean(item));
  return parts.join(" · ");
}

export function CitationShelf({ sources }: CitationShelfProps) {
  if (!sources.length) {
    return null;
  }

  const visible = sources.slice(0, 5);
  const hiddenCount = Math.max(0, sources.length - visible.length);
  const coverage = coverageLabel(sources);
  const verified = hasVerifiedSource(sources);
  const shelfLabel = verified ? "Sources" : "Search candidates";

  return (
    <section aria-label={shelfLabel} className="mt-3 border-t border-border/70 pt-3">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs font-medium text-muted-foreground">
        <div className="flex items-center gap-2">
          <Link2 className="h-3.5 w-3.5" aria-hidden />
          {shelfLabel}
        </div>
        {coverage ? (
          <Badge variant="outline" className="text-[0.65rem] font-normal">
            {coverage}
          </Badge>
        ) : null}
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        {visible.map((source, index) => (
          <a
            key={source.canonicalUrl}
            href={source.url}
            target="_blank"
            rel="noopener noreferrer"
            className="group rounded-md border border-border/75 bg-background/70 p-2.5 text-left no-underline transition-colors hover:border-sky-500/50 hover:bg-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <div className="flex items-start gap-2">
              <span className="flex h-5 min-w-5 items-center justify-center rounded-full bg-muted font-mono text-[0.65rem] text-muted-foreground">
                {index + 1}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex min-w-0 items-center gap-1.5">
                  <span className="truncate text-xs font-medium text-foreground">
                    {sourceLabel(source, index)}
                  </span>
                  <ExternalLink
                    className="h-3 w-3 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100"
                    aria-hidden
                  />
                </div>
                {source.domain ? (
                  <div className="mt-0.5 truncate text-[0.7rem] text-muted-foreground">
                    {source.domain}
                  </div>
                ) : null}
                {source.excerpt ? (
                  <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                    {source.excerpt}
                  </p>
                ) : null}
              </div>
              <Badge variant="secondary" className="shrink-0 text-[0.65rem]">
                {sourceBadge(source)}
              </Badge>
            </div>
          </a>
        ))}
      </div>
      {hiddenCount > 0 ? (
        <div className="mt-2 text-xs text-muted-foreground">
          +{hiddenCount} more {verified ? "sources" : "candidates"}
        </div>
      ) : null}
    </section>
  );
}
