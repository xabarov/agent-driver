import { describe, expect, test } from "vitest";

import { currentPlanStepTitle, parsePlanningSnapshot } from "../src/lib/planning";

describe("parsePlanningSnapshot", () => {
  test("parses valid snapshot", () => {
    const snapshot = parsePlanningSnapshot({
      todos: [
        { id: "s1", content: "Search web", status: "in_progress" },
        { id: "s2", content: "Summarize", status: "pending" },
        { id: "s3", content: "Done step", status: "completed" },
      ],
      completed: 1,
      total: 3,
      in_progress_id: "s1",
      plan_title: "Search web",
    });
    expect(snapshot).toBeDefined();
    expect(snapshot?.total).toBe(3);
    expect(snapshot?.completed).toBe(1);
    expect(snapshot?.todos).toHaveLength(3);
    expect(snapshot?.inProgressId).toBe("s1");
    expect(snapshot?.planTitle).toBe("Search web");
  });

  test("returns undefined for empty todos", () => {
    expect(parsePlanningSnapshot({ todos: [] })).toBeUndefined();
    expect(parsePlanningSnapshot(null)).toBeUndefined();
  });

  test("defaults invalid status to pending", () => {
    const snapshot = parsePlanningSnapshot({
      todos: [{ id: "x", content: "Step", status: "unknown" }],
    });
    expect(snapshot?.todos[0]?.status).toBe("pending");
  });
});

describe("currentPlanStepTitle", () => {
  test("prefers planTitle", () => {
    const snapshot = parsePlanningSnapshot({
      todos: [{ id: "a", content: "Long content", status: "in_progress" }],
      plan_title: "Short title",
    });
    expect(currentPlanStepTitle(snapshot!)).toBe("Short title");
  });
});
