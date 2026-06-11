import { useEffect, useMemo, useState } from "react";
import {
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDashed,
  Network,
  ShieldAlert,
} from "lucide-react";

import { cn } from "../../lib/cn";
import type { SubagentChildRun, ToolChatMessage } from "../../store/chatStore";
import { Badge } from "../ui/badge";

interface SubagentPanelProps {
  message: ToolChatMessage;
}

interface DelegatedTask {
  title: string;
  kind?: string;
  mode?: string;
}

function textValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function taskFromArgs(args: Record<string, unknown> | undefined): DelegatedTask {
  if (!args) {
    return { title: "Delegated work" };
  }
  const metadata = typeof args.metadata === "object" && args.metadata !== null
    ? (args.metadata as Record<string, unknown>)
    : undefined;
  return {
    title:
      textValue(args.description) ??
      textValue(args.task) ??
      textValue(metadata?.description) ??
      "Delegated work",
    kind: textValue(args.task_type) ?? textValue(metadata?.task_type),
    mode: textValue(args.execution_mode),
  };
}

type PanelStatus = ToolChatMessage["status"] | "joined" | "waiting" | "cancelled";

function panelStatus(message: ToolChatMessage): PanelStatus {
  const groupStatus = message.subagent?.groupStatus;
  if (groupStatus === "joined" || groupStatus === "waiting" || groupStatus === "cancelled") {
    return groupStatus;
  }
  if (groupStatus === "failed") {
    return "failed";
  }
  if (groupStatus === "running") {
    return "running";
  }
  return message.status;
}

function statusCopy(status: PanelStatus): string {
  if (status === "running") {
    return "running";
  }
  if (status === "waiting") {
    return "waiting";
  }
  if (status === "cancelled") {
    return "cancelled";
  }
  if (status === "failed") {
    return "failed";
  }
  if (status === "denied") {
    return "blocked";
  }
  return "joined";
}

function statusClass(status: PanelStatus): string {
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

function StatusIcon({ status }: { status: ToolChatMessage["status"] }) {
  if (status === "running") {
    return <CircleDashed className="h-4 w-4 animate-spin text-blue-500" aria-hidden />;
  }
  if (status === "failed" || status === "denied") {
    return <ShieldAlert className="h-4 w-4 text-amber-500" aria-hidden />;
  }
  return <CheckCircle2 className="h-4 w-4 text-emerald-500" aria-hidden />;
}

function childStatusAsToolStatus(status: SubagentChildRun["status"]): ToolChatMessage["status"] {
  if (status === "failed" || status === "cancelled") {
    return "failed";
  }
  if (status === "completed") {
    return "done";
  }
  return "running";
}

export function SubagentPanel({ message }: SubagentPanelProps) {
  const [open, setOpen] = useState(message.status === "running" || message.status === "denied");
  const task = useMemo(() => taskFromArgs(message.args), [message.args]);
  const status = panelStatus(message);
  const children = message.subagent?.childRuns ?? [];
  const rawPayload = useMemo(
    () =>
      JSON.stringify(
        {
          input: message.args,
          result: message.resultPreview,
        },
        null,
        2,
      ),
    [message.args, message.resultPreview],
  );

  useEffect(() => {
    if (message.status === "done") {
      setOpen(false);
    }
  }, [message.status]);

  return (
    <section
      aria-label="Delegated subagent work"
      className={cn(
        "ml-9 max-w-[min(100%,58rem)] rounded-lg border p-3 text-sm shadow-sm shadow-black/5",
        "border-border/80 bg-card/75 dark:bg-muted/20 dark:shadow-none",
      )}
    >
      <span className="sr-only" role="status" aria-live="polite">
        Delegated work status: {statusCopy(status)}
      </span>
      <button
        type="button"
        className="flex w-full items-center gap-2 rounded-md text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        aria-expanded={open}
        aria-label={`${open ? "Collapse" : "Inspect"} delegated subagent work`}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        <Network className="h-4 w-4 text-muted-foreground" aria-hidden />
        <span className="min-w-0 flex-1 truncate font-medium">Delegated work</span>
        <Badge className={cn("shrink-0", statusClass(status))} variant="secondary">
          {statusCopy(status)}
        </Badge>
        {message.durationMs != null ? (
          <span className="hidden text-xs text-muted-foreground sm:inline">{message.durationMs}ms</span>
        ) : null}
      </button>

      <div className="mt-3 rounded-md border border-border/60 bg-background/65 px-3 py-2">
        <div className="flex items-start gap-2">
          <div className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary/10">
            <Bot className="h-3.5 w-3.5 text-primary" aria-hidden />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <p className="min-w-0 font-medium text-foreground">{task.title}</p>
              {task.kind ? <Badge variant="outline">{task.kind}</Badge> : null}
              {task.mode ? <Badge variant="outline">{task.mode}</Badge> : null}
            </div>
            {message.resultPreview ? (
              <p className="mt-1 line-clamp-2 break-words text-xs leading-5 text-muted-foreground">
                {message.resultPreview}
              </p>
            ) : (
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                The parent agent delegated bounded work to a child agent.
              </p>
            )}
          </div>
          <StatusIcon status={message.status} />
        </div>
      </div>

      {children.length ? (
        <div className="mt-2 space-y-1.5" aria-label="Child agent runs">
          {children.map((child) => (
            <div
              key={`${child.taskId}:${child.subagentRunId ?? child.childRunId ?? ""}`}
              className="flex items-start gap-2 rounded-md border border-border/50 bg-background/45 px-3 py-2 text-xs"
              aria-label={`Child agent ${child.description ?? child.taskId} ${child.status}`}
            >
              <StatusIcon status={childStatusAsToolStatus(child.status)} />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-foreground">{child.description ?? child.taskId}</span>
                  <Badge variant="outline">{child.status}</Badge>
                  {child.childRunId ? (
                    <span
                      className="font-mono text-[0.65rem] text-muted-foreground"
                      title="Child run id"
                    >
                      {child.childRunId}
                    </span>
                  ) : null}
                </div>
                {child.outputPreview ? (
                  <p className="mt-1 line-clamp-2 break-words leading-5 text-muted-foreground">
                    {child.outputPreview}
                  </p>
                ) : null}
                {child.warning ? (
                  <p className="mt-1 break-words text-amber-700 dark:text-amber-300">
                    {child.warning}
                  </p>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {open ? (
        <div className="mt-3 space-y-2">
          {message.args?.task ? (
            <div className="rounded-md border bg-background/80 p-2 text-xs text-foreground">
              <div className="mb-1 text-[0.65rem] font-semibold uppercase text-muted-foreground">
                Child brief
              </div>
              <p className="whitespace-pre-wrap break-words">{String(message.args.task)}</p>
            </div>
          ) : null}
          {children.length ? (
            <div className="rounded-md border bg-background/80 p-2 text-xs text-foreground">
              <div className="mb-1 text-[0.65rem] font-semibold uppercase text-muted-foreground">
                Child results
              </div>
              <div className="space-y-2">
                {children.map((child) => (
                  <div
                    key={`detail:${child.taskId}:${child.subagentRunId ?? child.childRunId ?? ""}`}
                    className="rounded-sm border border-border/50 bg-background/60 p-2"
                  >
                    <dl className="grid gap-1 sm:grid-cols-[6rem_1fr]">
                      <dt className="text-muted-foreground">Task</dt>
                      <dd className="break-words">{child.description ?? child.taskId}</dd>
                      <dt className="text-muted-foreground">Status</dt>
                      <dd>{child.status}</dd>
                      {child.childRunId ? (
                        <>
                          <dt className="text-muted-foreground">Run id</dt>
                          <dd className="break-all font-mono">{child.childRunId}</dd>
                        </>
                      ) : null}
                      {child.subagentRunId ? (
                        <>
                          <dt className="text-muted-foreground">Subagent id</dt>
                          <dd className="break-all font-mono">{child.subagentRunId}</dd>
                        </>
                      ) : null}
                      {child.usedTools?.length ? (
                        <>
                          <dt className="text-muted-foreground">Used tools</dt>
                          <dd className="break-words">{child.usedTools.join(", ")}</dd>
                        </>
                      ) : null}
                    </dl>
                    {child.outputPreview ? (
                      <p className="mt-2 whitespace-pre-wrap break-words leading-5">
                        {child.outputPreview}
                      </p>
                    ) : null}
                    {child.warning ? (
                      <p className="mt-2 whitespace-pre-wrap break-words text-amber-700 dark:text-amber-300">
                        {child.warning}
                      </p>
                    ) : null}
                  </div>
                ))}
              </div>
            </div>
          ) : null}
          <details className="rounded-md border bg-background/80 p-2 text-xs text-foreground">
            <summary className="cursor-pointer text-[0.65rem] font-semibold uppercase text-muted-foreground">
              Debug payload
            </summary>
            <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words font-mono">
              {rawPayload}
            </pre>
          </details>
        </div>
      ) : null}
    </section>
  );
}
