import { describe, expect, test } from "vitest";

import { eventsToMessages, type RunStreamEvent } from "../src/lib/events";

describe("eventsToMessages planning", () => {
  test("strips streamed text-form tool calls across token chunks", () => {
    const events: RunStreamEvent<Record<string, unknown>>[] = [
      {
        schema_version: "1.0",
        stream_id: "r:1",
        run_id: "r",
        attempt_id: "a",
        seq: 1,
        event: "run_started",
        source: "runtime_event",
        data: {},
      },
      {
        schema_version: "1.0",
        stream_id: "r:2",
        run_id: "r",
        attempt_id: "a",
        seq: 2,
        event: "token_delta",
        source: "runtime_event",
        data: { delta_text: "Before\n<tool_call>{" },
      },
      {
        schema_version: "1.0",
        stream_id: "r:3",
        run_id: "r",
        attempt_id: "a",
        seq: 3,
        event: "token_delta",
        source: "runtime_event",
        data: { delta_text: '"name":"todo_write"}' },
      },
      {
        schema_version: "1.0",
        stream_id: "r:4",
        run_id: "r",
        attempt_id: "a",
        seq: 4,
        event: "token_delta",
        source: "runtime_event",
        data: { delta_text: "</tool_call>\nAfter" },
      },
      {
        schema_version: "1.0",
        stream_id: "r:5",
        run_id: "r",
        attempt_id: "a",
        seq: 5,
        event: "run_completed",
        source: "runtime_event",
        data: {},
      },
    ];

    const messages = eventsToMessages(events);
    expect(messages).toHaveLength(1);
    expect(messages[0]).toMatchObject({ role: "assistant", content: "Before\nAfter" });
  });

  test("attaches planning_snapshot to assistant and skips todo_write tools", () => {
    const events: RunStreamEvent<Record<string, unknown>>[] = [
      {
        schema_version: "1.0",
        stream_id: "r:1",
        run_id: "r",
        attempt_id: "a",
        seq: 1,
        event: "run_started",
        source: "runtime_event",
        data: {},
      },
      {
        schema_version: "1.0",
        stream_id: "r:2",
        run_id: "r",
        attempt_id: "a",
        seq: 2,
        event: "token_delta",
        source: "runtime_event",
        data: { delta_text: "Planning..." },
      },
      {
        schema_version: "1.0",
        stream_id: "r:3",
        run_id: "r",
        attempt_id: "a",
        seq: 3,
        event: "tool_call_completed",
        source: "runtime_event",
        data: {
          planning_snapshot: {
            todos: [
              { id: "s1", content: "Step one", status: "in_progress" },
              { id: "s2", content: "Step two", status: "pending" },
            ],
            completed: 0,
            total: 2,
            in_progress_id: "s1",
          },
          tools: [
            {
              tool_name: "todo_write",
              tool_call_id: "tw1",
              status: "ok",
            },
            {
              tool_name: "file_write",
              tool_call_id: "fw1",
              status: "ok",
            },
          ],
        },
      },
      {
        schema_version: "1.0",
        stream_id: "r:4",
        run_id: "r",
        attempt_id: "a",
        seq: 4,
        event: "run_completed",
        source: "runtime_event",
        data: {},
      },
    ];

    const messages = eventsToMessages(events);
    const assistant = messages.find((m) => m.role === "assistant");
    expect(assistant?.role).toBe("assistant");
    if (assistant?.role === "assistant") {
      expect(assistant.planningSnapshot?.total).toBe(2);
      expect(assistant.content).toContain("Planning");
    }
    expect(messages.some((m) => m.role === "tool" && m.name === "todo_write")).toBe(false);
    expect(messages.some((m) => m.role === "tool" && m.name === "file_write")).toBe(true);
  });
});
