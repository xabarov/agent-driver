import { useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  FileText,
  Globe,
  ShieldAlert,
  Terminal,
  Wrench,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { cn } from "../../lib/cn";
import type { ToolChatMessage } from "../../store/chatStore";
import { Badge } from "../ui/badge";

interface ToolCallCardProps {
  message: ToolChatMessage;
}

function toolIcon(name: string): LucideIcon {
  if (name.includes("web") || name.includes("search") || name.includes("fetch")) {
    return Globe;
  }
  if (name.includes("shell") || name.includes("bash")) {
    return Terminal;
  }
  if (name.includes("file") || name.includes("read") || name.includes("write")) {
    return FileText;
  }
  return Wrench;
}

function statusClass(status: ToolChatMessage["status"]): string {
  if (status === "running") {
    return "bg-blue-500/15 text-blue-700 dark:text-blue-300";
  }
  if (status === "failed") {
    return "bg-destructive/15 text-destructive";
  }
  if (status === "denied") {
    return "bg-amber-500/15 text-amber-700 dark:text-amber-300";
  }
  return "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300";
}

export function ToolCallCard({ message }: ToolCallCardProps) {
  const [open, setOpen] = useState(
    message.status === "running" || message.status === "denied",
  );
  const Icon = message.status === "denied" ? ShieldAlert : toolIcon(message.name);

  return (
    <div className="ml-9 max-w-[min(100%,58rem)] rounded-lg border border-border/80 bg-card/70 p-3 text-sm shadow-sm shadow-black/5 dark:bg-muted/20 dark:shadow-none">
      <button
        type="button"
        className="flex w-full items-center gap-2 rounded-md text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        aria-expanded={open}
        aria-label={`${open ? "Collapse" : "Expand"} tool call ${message.name}`}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        <Icon className="h-4 w-4 text-muted-foreground" />
        <span className="min-w-0 truncate font-mono text-xs font-medium">{message.name}</span>
        <Badge
          variant="secondary"
          className={cn(
            "shrink-0",
            message.status === "running" && "animate-pulse",
            statusClass(message.status),
          )}
        >
          {message.status}
        </Badge>
        {message.risk ? <Badge variant="outline">{message.risk}</Badge> : null}
        {message.durationMs != null ? (
          <span className="ml-auto text-xs text-muted-foreground">{message.durationMs}ms</span>
        ) : null}
      </button>
      {message.argsSummary ? (
        <p className="mt-2 truncate text-xs text-muted-foreground">{message.argsSummary}</p>
      ) : null}
      {message.resultPreview && !open ? (
        <p className="mt-2 truncate text-xs text-foreground/80">{message.resultPreview}</p>
      ) : null}
      {open ? (
        <div className="mt-3 space-y-2">
          {message.args ? (
            <pre className="overflow-x-auto rounded-md border bg-background/80 p-2 text-xs text-foreground">
              {JSON.stringify(message.args, null, 2)}
            </pre>
          ) : null}
          {message.resultPreview ? (
            <pre className="overflow-x-auto rounded-md border bg-background/80 p-2 text-xs text-foreground">
              {message.resultPreview}
            </pre>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
