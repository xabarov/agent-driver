import { useEffect, useState } from "react";
import {
  Calculator,
  ChevronDown,
  ChevronRight,
  FileText,
  Globe,
  Search,
  ShieldAlert,
  Terminal,
  Wrench,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { cn } from "../../lib/cn";
import type { ToolChatMessage } from "../../store/chatStore";
import { Badge } from "../ui/badge";
import { CitationShelf } from "./CitationShelf";
import { SubagentPanel } from "./SubagentPanel";

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

function firstCodeLine(args?: Record<string, unknown>): string {
  const code = typeof args?.code === "string" ? args.code.trim() : "";
  if (!code) {
    return "Python code";
  }
  return (
    code
      .split("\n")
      .find((line) => line.trim())
      ?.trim() ?? "Python code"
  );
}

interface PythonResultChips {
  result: string;
  kind: "exact" | "rounded";
}

function looksLikePythonError(text: string): boolean {
  return /\b(error|exception|traceback|policy|blocked|timeout|failed|denied)\b/i.test(
    text,
  );
}

function compactResultValue(text: string): string | undefined {
  const trimmed = text.trim();
  if (!trimmed || looksLikePythonError(trimmed)) {
    return undefined;
  }
  const resultMatch = trimmed.match(
    /(?:^|\b)(?:result|answer|value|stdout)\s*[:=]\s*([^\n;,]+)/i,
  );
  const candidate = (resultMatch?.[1] ?? trimmed.split("\n").find(Boolean) ?? "").trim();
  if (!candidate || candidate.length > 48) {
    return undefined;
  }
  const numeric = candidate.match(/[≈~]?[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:e[-+]?\d+)?%?/i);
  if (!numeric) {
    return undefined;
  }
  return numeric[0];
}

function pythonResultChips(resultPreview?: string): PythonResultChips[] {
  if (!resultPreview) {
    return [];
  }
  const result = compactResultValue(resultPreview);
  if (!result) {
    return [];
  }
  const kind =
    result.includes("≈") ||
    result.includes("~") ||
    /approx|rounded|примерно|округл/i.test(resultPreview)
      ? "rounded"
      : "exact";
  return [{ result, kind }];
}

function stringArg(
  args: Record<string, unknown> | undefined,
  key: string,
): string | undefined {
  const value = args?.[key];
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function hostnameFromUrl(url: string | undefined): string | undefined {
  if (!url) {
    return undefined;
  }
  try {
    return new URL(url).hostname.replace(/^www\./i, "");
  } catch {
    return undefined;
  }
}

function DebugPayload({ args }: { args?: Record<string, unknown> }) {
  if (!args) {
    return null;
  }
  return (
    <details className="rounded-md border bg-background/80 p-2 text-xs text-foreground">
      <summary className="cursor-pointer text-[0.65rem] font-semibold uppercase text-muted-foreground">
        Debug payload
      </summary>
      <pre className="mt-2 whitespace-pre-wrap break-words font-mono">
        {JSON.stringify(args, null, 2)}
      </pre>
    </details>
  );
}

function WebEvidencePanel({ message }: ToolCallCardProps) {
  const [open, setOpen] = useState(
    message.status === "running" || message.status === "failed",
  );
  const isSearch = message.name === "web_search";
  const query = stringArg(message.args, "query");
  const url = stringArg(message.args, "url");
  const domain = hostnameFromUrl(url) ?? message.sources?.[0]?.domain;
  const title = isSearch ? "Web search" : "Web fetch";
  const summary =
    (isSearch && query ? query : undefined) ??
    (!isSearch && (domain ?? url) ? (domain ?? url) : undefined) ??
    message.argsSummary ??
    (isSearch ? "Search query" : "Fetched page");
  const Icon = isSearch ? Search : Globe;
  const sourceCount = message.sources?.length ?? 0;

  useEffect(() => {
    if (message.status === "done") {
      setOpen(false);
    }
  }, [message.status]);

  return (
    <div className="ml-9 max-w-[min(100%,58rem)] rounded-lg border border-sky-500/20 bg-sky-500/[0.035] p-3 text-sm shadow-sm shadow-black/5 dark:border-sky-300/15 dark:bg-sky-300/[0.045] dark:shadow-none">
      <button
        type="button"
        className="flex w-full items-center gap-2 rounded-md text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        aria-expanded={open}
        aria-label={`${open ? "Collapse" : "Inspect"} ${title.toLowerCase()} evidence`}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? (
          <ChevronDown className="h-4 w-4" />
        ) : (
          <ChevronRight className="h-4 w-4" />
        )}
        <Icon className="h-4 w-4 text-sky-600 dark:text-sky-300" />
        <span className="min-w-0 font-medium">{title}</span>
        <span className="rounded border border-border/80 bg-background/70 px-1.5 py-0.5 font-mono text-[0.68rem] text-muted-foreground">
          {message.name}
        </span>
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
        {sourceCount > 0 ? (
          <Badge variant="outline" className="hidden shrink-0 sm:inline-flex">
            {sourceCount} {sourceCount === 1 ? "source" : "sources"}
          </Badge>
        ) : null}
        {message.durationMs != null ? (
          <span className="ml-auto text-xs text-muted-foreground">
            {message.durationMs}ms
          </span>
        ) : null}
      </button>
      <div className="mt-2 grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-start">
        <p className="min-w-0 break-words text-xs text-muted-foreground">
          {isSearch ? "Query" : "Target"}:{" "}
          <span className="text-foreground/90">{summary}</span>
        </p>
        {domain ? (
          <span className="w-fit rounded-full border border-border/70 bg-background/60 px-2 py-0.5 text-[0.7rem] text-muted-foreground">
            {domain}
          </span>
        ) : null}
      </div>
      {message.resultPreview && !open ? (
        <p className="mt-2 line-clamp-2 break-words text-xs text-foreground/80">
          {message.resultPreview}
        </p>
      ) : null}
      {open ? (
        <div className="mt-3 space-y-2">
          {message.resultPreview ? (
            <div className="rounded-md border border-border/70 bg-background/75 px-3 py-2 text-xs text-foreground">
              <div className="mb-1 text-[0.65rem] font-semibold uppercase text-muted-foreground">
                Summary
              </div>
              <p className="whitespace-pre-wrap break-words">{message.resultPreview}</p>
            </div>
          ) : null}
          {message.sources?.length ? <CitationShelf sources={message.sources} /> : null}
          <DebugPayload args={message.args} />
        </div>
      ) : null}
    </div>
  );
}

function PythonExecutionPanel({ message }: ToolCallCardProps) {
  const [open, setOpen] = useState(
    message.status === "running" || message.status === "failed",
  );
  const resultChips = pythonResultChips(message.resultPreview);
  const code = typeof message.args?.code === "string" ? message.args.code.trim() : "";
  const sessionId =
    typeof message.args?.session_id === "string" && message.args.session_id.trim()
      ? message.args.session_id.trim()
      : undefined;

  useEffect(() => {
    if (message.status === "done") {
      setOpen(false);
    }
  }, [message.status]);

  return (
    <div
      className="ml-9 max-w-[min(100%,58rem)] rounded-lg border border-border/80 bg-card/75 p-3 text-sm shadow-sm shadow-black/5 dark:bg-muted/20 dark:shadow-none"
      role="group"
      aria-label="Python execution"
    >
      <span className="sr-only" role="status" aria-live="polite">
        Python execution {message.status}
      </span>
      <button
        type="button"
        className="flex w-full items-center gap-2 rounded-md text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        aria-expanded={open}
        aria-label={`${open ? "Collapse" : "Expand"} Python execution`}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? (
          <ChevronDown className="h-4 w-4" />
        ) : (
          <ChevronRight className="h-4 w-4" />
        )}
        <Calculator className="h-4 w-4 text-muted-foreground" />
        <span className="min-w-0 truncate font-medium">Python calculation</span>
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
        {message.durationMs != null ? (
          <span className="ml-auto text-xs text-muted-foreground">
            {message.durationMs}ms
          </span>
        ) : null}
      </button>
      <div className="mt-2 space-y-2">
        <p className="break-words text-xs text-muted-foreground">
          {firstCodeLine(message.args)}
        </p>
        {resultChips.length ? (
          <div className="flex flex-wrap gap-1.5" aria-label="Python result summary">
            {resultChips.map((chip) => (
              <span
                key={`${chip.kind}:${chip.result}`}
                className="inline-flex max-w-full items-center gap-1 rounded-full border border-border/70 bg-background/70 px-2 py-0.5 text-[0.7rem] text-foreground"
              >
                <span className="font-semibold text-muted-foreground">
                  {chip.kind === "exact" ? "exact" : "rounded"}
                </span>
                <span className="truncate font-mono">{chip.result}</span>
              </span>
            ))}
          </div>
        ) : null}
        {message.resultPreview ? (
          <div className="rounded-md border border-border/70 bg-background/70 px-3 py-2">
            <div className="mb-1 text-[0.65rem] font-semibold uppercase text-muted-foreground">
              Result
            </div>
            <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words font-sans text-xs text-foreground/90">
              {message.resultPreview}
            </pre>
          </div>
        ) : null}
      </div>
      {open ? (
        <div className="mt-3 space-y-2">
          {code ? (
            <div className="rounded-md border bg-background/80 p-2 text-xs text-foreground">
              <div className="mb-1 text-[0.65rem] font-semibold uppercase text-muted-foreground">
                Code
              </div>
              <pre className="max-h-56 overflow-y-auto whitespace-pre-wrap break-words font-mono">
                {code}
              </pre>
            </div>
          ) : null}
          <div className="rounded-md border bg-background/80 p-2 text-xs text-muted-foreground">
            <div className="mb-1 text-[0.65rem] font-semibold uppercase">Execution</div>
            <dl className="grid gap-1 sm:grid-cols-2">
              <div>
                <dt className="font-medium text-foreground">Tool</dt>
                <dd>Sandboxed Python</dd>
              </div>
              <div>
                <dt className="font-medium text-foreground">Session</dt>
                <dd>{sessionId ?? "run scoped"}</dd>
              </div>
            </dl>
          </div>
          <DebugPayload args={message.args} />
        </div>
      ) : null}
    </div>
  );
}

export function ToolCallCard({ message }: ToolCallCardProps) {
  if (message.name === "agent_tool") {
    return <SubagentPanel message={message} />;
  }
  if (message.name === "python") {
    return <PythonExecutionPanel message={message} />;
  }
  if (message.name === "web_search" || message.name === "web_fetch") {
    return <WebEvidencePanel message={message} />;
  }

  const [open, setOpen] = useState(
    message.status === "running" || message.status === "denied",
  );
  const Icon = message.status === "denied" ? ShieldAlert : toolIcon(message.name);

  useEffect(() => {
    if (message.status === "done") {
      setOpen(false);
    }
  }, [message.status]);

  return (
    <div className="ml-9 max-w-[min(100%,58rem)] rounded-lg border border-border/80 bg-card/70 p-3 text-sm shadow-sm shadow-black/5 dark:bg-muted/20 dark:shadow-none">
      <button
        type="button"
        className="flex w-full items-center gap-2 rounded-md text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        aria-expanded={open}
        aria-label={`${open ? "Collapse" : "Expand"} tool call ${message.name}`}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? (
          <ChevronDown className="h-4 w-4" />
        ) : (
          <ChevronRight className="h-4 w-4" />
        )}
        <Icon className="h-4 w-4 text-muted-foreground" />
        <span className="min-w-0 truncate font-mono text-xs font-medium">
          {message.name}
        </span>
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
          <span className="ml-auto text-xs text-muted-foreground">
            {message.durationMs}ms
          </span>
        ) : null}
      </button>
      {message.argsSummary ? (
        <p className="mt-2 break-words text-xs text-muted-foreground">
          {message.argsSummary}
        </p>
      ) : null}
      {message.resultPreview && !open ? (
        <p className="mt-2 line-clamp-2 break-words text-xs text-foreground/80">
          {message.resultPreview}
        </p>
      ) : null}
      {open ? (
        <div className="mt-3 space-y-2">
          {message.args ? (
            <div className="rounded-md border bg-background/80 p-2 text-xs text-foreground">
              <div className="mb-1 text-[0.65rem] font-semibold uppercase text-muted-foreground">
                Input
              </div>
              <pre className="whitespace-pre-wrap break-words font-mono">
                {JSON.stringify(message.args, null, 2)}
              </pre>
            </div>
          ) : null}
          {message.resultPreview ? (
            <div className="rounded-md border bg-background/80 p-2 text-xs text-foreground">
              <div className="mb-1 text-[0.65rem] font-semibold uppercase text-muted-foreground">
                Result
              </div>
              <pre className="whitespace-pre-wrap break-words font-sans">
                {message.resultPreview}
              </pre>
            </div>
          ) : null}
          {message.sources?.length ? <CitationShelf sources={message.sources} /> : null}
        </div>
      ) : null}
    </div>
  );
}
