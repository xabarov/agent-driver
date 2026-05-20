import { describe, expect, it } from "vitest";

import { filterSessions, groupSessionsByDate } from "../src/lib/sessionGroups";
import type { SessionSummaryView } from "../src/types/api";

function session(id: string, title: string, updatedAt: string): SessionSummaryView {
  return {
    session_id: id,
    thread_id: `thread_${id}`,
    title,
    updated_at: updatedAt,
    runs_count: 1,
  };
}

describe("sessionGroups", () => {
  it("filters sessions by title", () => {
    const sessions = [
      session("a", "hello world", new Date().toISOString()),
      session("b", "other", new Date().toISOString()),
    ];
    expect(filterSessions(sessions, "hello")).toHaveLength(1);
  });

  it("groups sessions into today and older", () => {
    const now = new Date();
    const sessions = [
      session("today", "today", now.toISOString()),
      session("old", "old", "2020-01-01T00:00:00Z"),
    ];
    const groups = groupSessionsByDate(sessions);
    expect(groups.some((group) => group.label === "Today")).toBe(true);
    expect(groups.some((group) => group.label === "Older")).toBe(true);
  });
});
