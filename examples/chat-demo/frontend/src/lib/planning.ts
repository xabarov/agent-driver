export type PlanningTodoStatus = "pending" | "in_progress" | "completed" | "cancelled";

export interface PlanningTodo {
  id: string;
  content: string;
  status: PlanningTodoStatus;
}

export interface PlanningSnapshot {
  todos: PlanningTodo[];
  inProgressId: string | null;
  completed: number;
  total: number;
  planTitle: string | null;
}

const VALID_STATUSES = new Set<PlanningTodoStatus>([
  "pending",
  "in_progress",
  "completed",
  "cancelled",
]);

function parseTodoStatus(raw: unknown): PlanningTodoStatus {
  if (typeof raw === "string" && VALID_STATUSES.has(raw as PlanningTodoStatus)) {
    return raw as PlanningTodoStatus;
  }
  return "pending";
}

function parseTodo(row: unknown): PlanningTodo | null {
  if (!row || typeof row !== "object") {
    return null;
  }
  const record = row as Record<string, unknown>;
  const id = String(record.id ?? record.todo_id ?? "").trim();
  const content = String(record.content ?? "").trim();
  if (!id || !content) {
    return null;
  }
  return {
    id,
    content,
    status: parseTodoStatus(record.status),
  };
}

export function parsePlanningSnapshot(raw: unknown): PlanningSnapshot | undefined {
  if (!raw || typeof raw !== "object") {
    return undefined;
  }
  const record = raw as Record<string, unknown>;
  const todosRaw = record.todos;
  if (!Array.isArray(todosRaw) || todosRaw.length === 0) {
    return undefined;
  }
  const todos = todosRaw.map(parseTodo).filter((item): item is PlanningTodo => item !== null);
  if (todos.length === 0) {
    return undefined;
  }
  const completed = Number(record.completed);
  const total = Number(record.total);
  const inProgressRaw = record.in_progress_id;
  const planTitleRaw = record.plan_title;
  return {
    todos,
    inProgressId: typeof inProgressRaw === "string" && inProgressRaw ? inProgressRaw : null,
    completed: Number.isFinite(completed) ? completed : todos.filter((t) => t.status === "completed").length,
    total: Number.isFinite(total) && total > 0 ? total : todos.length,
    planTitle: typeof planTitleRaw === "string" && planTitleRaw.trim() ? planTitleRaw.trim() : null,
  };
}

export function currentPlanStepTitle(snapshot: PlanningSnapshot): string | null {
  if (snapshot.planTitle) {
    return snapshot.planTitle;
  }
  if (snapshot.inProgressId) {
    const item = snapshot.todos.find((todo) => todo.id === snapshot.inProgressId);
    if (item?.content) {
      return item.content;
    }
  }
  return null;
}
