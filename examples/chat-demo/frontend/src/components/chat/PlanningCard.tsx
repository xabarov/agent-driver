import { useState } from "react";
import { Ban, Check, ChevronDown, ChevronRight, Circle, Loader2 } from "lucide-react";

import { cn } from "../../lib/cn";
import {
  currentPlanStepTitle,
  planningProgressPercent,
  type PlanningSnapshot,
  type PlanningTodo,
} from "../../lib/planning";

interface PlanningCardProps {
  snapshot: PlanningSnapshot;
  streaming?: boolean;
}

function TodoStatusIcon({ todo, streaming }: { todo: PlanningTodo; streaming?: boolean }) {
  if (todo.status === "completed") {
    return <Check className="h-4 w-4 shrink-0 text-emerald-500" aria-hidden />;
  }
  if (todo.status === "in_progress") {
    return (
      <Loader2
        className={cn("h-4 w-4 shrink-0 text-primary", streaming && "animate-spin")}
        aria-hidden
      />
    );
  }
  if (todo.status === "cancelled") {
    return <Ban className="h-4 w-4 shrink-0 text-muted-foreground/70" aria-hidden />;
  }
  return <Circle className="h-4 w-4 shrink-0 text-muted-foreground/50" aria-hidden />;
}

function todoRowClass(todo: PlanningTodo): string {
  if (todo.status === "in_progress") {
    return "border-primary/40 bg-primary/5";
  }
  if (todo.status === "completed") {
    return "border-transparent bg-transparent";
  }
  if (todo.status === "cancelled") {
    return "border-transparent opacity-60";
  }
  return "border-transparent";
}

export function PlanningCard({ snapshot, streaming }: PlanningCardProps) {
  const [open, setOpen] = useState(true);
  const progressPct = planningProgressPercent(snapshot, streaming);
  const stepTitle = currentPlanStepTitle(snapshot);
  const activeStepLabel =
    snapshot.inProgressIndex != null
      ? `Step ${snapshot.inProgressIndex} of ${snapshot.total}`
      : null;
  const collapsible = snapshot.todos.length > 6;

  return (
    <div
      className="mt-3 rounded-lg border border-border/80 bg-background/40"
      role="region"
      aria-label="Plan checklist"
    >
      <div className="flex items-start gap-2 border-b border-border/60 px-3 py-2">
        {collapsible ? (
          <button
            type="button"
            className="mt-0.5 shrink-0 text-muted-foreground hover:text-foreground"
            aria-expanded={open}
            onClick={() => setOpen((value) => !value)}
          >
            {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          </button>
        ) : null}
        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Plan
            </span>
            <span className="text-xs font-mono text-foreground">
              {snapshot.completed}/{snapshot.total} done
            </span>
            {activeStepLabel ? (
              <span className="text-xs text-muted-foreground">· {activeStepLabel}</span>
            ) : null}
          </div>
          <div
            className="h-1 overflow-hidden rounded-full bg-muted"
            role="progressbar"
            aria-valuenow={progressPct}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div
              className="h-full rounded-full bg-primary/80 transition-[width] duration-300"
              style={{ width: `${progressPct}%` }}
            />
          </div>
          {stepTitle ? (
            <p className="truncate text-xs text-muted-foreground" title={stepTitle}>
              Current: {stepTitle}
            </p>
          ) : null}
        </div>
      </div>
      {open ? (
        <ul className="space-y-1 px-2 py-2">
          {snapshot.todos.map((todo) => (
            <li
              key={todo.id}
              className={cn(
                "flex items-start gap-2 rounded-md border px-2 py-1.5 text-sm",
                todoRowClass(todo),
              )}
            >
              <TodoStatusIcon todo={todo} streaming={streaming} />
              <span
                className={cn(
                  "min-w-0 flex-1 leading-snug",
                  todo.status === "completed" && "text-muted-foreground line-through",
                  todo.status === "cancelled" && "text-muted-foreground line-through decoration-muted-foreground/50",
                  todo.status === "in_progress" && "font-medium text-foreground",
                )}
                title={todo.content}
              >
                {todo.content}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
