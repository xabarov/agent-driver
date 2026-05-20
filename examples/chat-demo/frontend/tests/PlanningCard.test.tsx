import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { PlanningCard } from "../src/components/chat/PlanningCard";
import type { PlanningSnapshot } from "../src/lib/planning";

const snapshot: PlanningSnapshot = {
  todos: [
    { id: "1", content: "Write game file", status: "completed" },
    { id: "2", content: "Add controls", status: "in_progress" },
    { id: "3", content: "Test run", status: "pending" },
  ],
  inProgressId: "2",
  completed: 1,
  total: 3,
  planTitle: "Add controls",
};

describe("PlanningCard", () => {
  test("shows progress and all todo statuses including completed", () => {
    render(<PlanningCard snapshot={snapshot} />);
    expect(screen.getByText("1/3 done")).toBeInTheDocument();
    expect(screen.getByText("Write game file")).toBeInTheDocument();
    expect(screen.getByText("Add controls")).toBeInTheDocument();
    expect(screen.getByText("Test run")).toBeInTheDocument();
    expect(screen.getByText(/Current: Add controls/)).toBeInTheDocument();
    const completed = screen.getByText("Write game file");
    expect(completed.className).toContain("line-through");
  });
});
