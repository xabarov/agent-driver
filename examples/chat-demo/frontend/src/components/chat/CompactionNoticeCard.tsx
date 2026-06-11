import { Archive, CheckCircle2, Loader2, TriangleAlert } from "lucide-react";

import { cn } from "../../lib/cn";
import type { CompactionNotice } from "../../store/chatStore";
import { Badge } from "../ui/badge";

interface CompactionNoticeCardProps {
  message: CompactionNotice;
}

function titleForStatus(status: CompactionNotice["status"]): string {
  if (status === "failed") {
    return "Memory compaction needs attention";
  }
  if (status === "done") {
    return "Conversation memory compacted";
  }
  return "Compacting conversation memory";
}

function detailForNotice(message: CompactionNotice): string {
  if (message.status === "failed") {
    return message.failureKind
      ? `The run continued, but compaction failed: ${message.failureKind}.`
      : "The run continued, but compaction could not finish.";
  }
  if (message.status === "done") {
    const count = message.summarizedMessageCount;
    return count && count > 0
      ? `Older context was summarized across ${count} messages.`
      : "Older context was summarized to keep the run within budget.";
  }
  return "Summarizing older context so the run can continue.";
}

export function CompactionNoticeCard({ message }: CompactionNoticeCardProps) {
  const Icon =
    message.status === "failed"
      ? TriangleAlert
      : message.status === "done"
        ? CheckCircle2
        : Loader2;
  return (
    <div className="ml-9 max-w-[min(100%,58rem)]">
      <div
        className={cn(
          "rounded-lg border px-3 py-2.5 text-sm shadow-sm shadow-black/5",
          "border-violet-500/20 bg-violet-500/[0.045] dark:border-violet-300/15 dark:bg-violet-300/[0.055]",
          message.status === "failed" &&
            "border-amber-500/30 bg-amber-500/[0.07] dark:border-amber-300/20 dark:bg-amber-300/[0.07]",
        )}
        role={message.status === "failed" ? "alert" : "status"}
      >
        <div className="flex items-start gap-2">
          <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-border/70 bg-background/70">
            <Icon
              className={cn(
                "h-3.5 w-3.5",
                message.status === "running" && "animate-spin text-violet-400",
                message.status === "done" && "text-emerald-500",
                message.status === "failed" && "text-amber-500",
              )}
              aria-hidden
            />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{titleForStatus(message.status)}</span>
              <Badge variant="outline" className="gap-1 text-[0.68rem]">
                <Archive className="h-3 w-3" aria-hidden />
                {message.mode ?? "auto"}
              </Badge>
            </div>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
              {detailForNotice(message)}
            </p>
            <details className="mt-2 text-xs text-muted-foreground">
              <summary className="cursor-pointer select-none font-medium text-foreground/80">
                Details
              </summary>
              <dl className="mt-2 grid gap-1 rounded-md border border-border/60 bg-background/60 p-2">
                <div className="flex min-w-0 gap-2">
                  <dt className="shrink-0 text-muted-foreground">id</dt>
                  <dd className="min-w-0 truncate font-mono">{message.compactionId}</dd>
                </div>
                {message.reason ? (
                  <div className="flex min-w-0 gap-2">
                    <dt className="shrink-0 text-muted-foreground">reason</dt>
                    <dd className="min-w-0 truncate">{message.reason}</dd>
                  </div>
                ) : null}
                {message.attempts ? (
                  <div className="flex min-w-0 gap-2">
                    <dt className="shrink-0 text-muted-foreground">attempts</dt>
                    <dd>{message.attempts}</dd>
                  </div>
                ) : null}
              </dl>
            </details>
          </div>
        </div>
      </div>
    </div>
  );
}
