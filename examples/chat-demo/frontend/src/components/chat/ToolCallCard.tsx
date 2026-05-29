import { useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  FileText,
  Globe,
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

export function ToolCallCard({ message }: ToolCallCardProps) {
  const [open, setOpen] = useState(message.status === "running");
  const Icon = toolIcon(message.name);

  return (
    <div className="ml-11 rounded-lg border border-border/80 bg-muted/20 p-3 text-sm">
      <button
        type="button"
        className="flex w-full items-center gap-2 text-left"
        onClick={() => setOpen((value) => !value)}
      >
        {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        <Icon className="h-4 w-4 text-muted-foreground" />
        <span className="font-medium">{message.name}</span>
        <Badge
          variant="secondary"
          className={cn(
            message.status === "running" && "animate-pulse",
            message.status === "failed" && "bg-destructive/20 text-destructive",
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
      {open ? (
        <div className="mt-3 space-y-2">
          {message.args ? (
            <pre className="overflow-x-auto rounded-md border bg-background/80 p-2 text-xs">
              {JSON.stringify(message.args, null, 2)}
            </pre>
          ) : null}
          {message.resultPreview ? (
            <pre className="overflow-x-auto rounded-md border bg-background/80 p-2 text-xs">
              {message.resultPreview}
            </pre>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
