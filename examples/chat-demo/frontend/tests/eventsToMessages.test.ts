import { describe, expect, it } from "vitest";

import { eventsToMessages, type RunStreamEvent } from "../src/lib/events";

function ev(
  event: string,
  seq: number,
  data: Record<string, unknown> = {},
): RunStreamEvent {
  return {
    schema_version: "1.0",
    stream_id: "s1",
    run_id: "r1",
    attempt_id: "a1",
    seq,
    event,
    source: "runtime_event",
    data,
  };
}

describe("eventsToMessages", () => {
  it("builds assistant text from token deltas", () => {
    const messages = eventsToMessages([
      ev("run_started", 1),
      ev("token_delta", 2, { delta_text: "Hi " }),
      ev("token_delta", 3, { delta_text: "there" }),
      ev("run_completed", 4),
    ]);
    expect(messages).toHaveLength(1);
    expect(messages[0]).toMatchObject({ role: "assistant", content: "Hi there" });
  });

  it("inserts tool messages between assistant chunks", () => {
    const messages = eventsToMessages([
      ev("run_started", 1),
      ev("token_delta", 2, { delta_text: "Before" }),
      ev("tool_call_started", 3, {
        tool_name: "web_search",
        tool_call_id: "tc1",
        args: { q: "test" },
      }),
      ev("tool_call_completed", 4, {
        tool_name: "web_search",
        tool_call_id: "tc1",
        result_summary: "ok",
      }),
      ev("token_delta", 5, { delta_text: "After" }),
      ev("run_completed", 6),
    ]);
    const roles = messages.map((m) => m.role);
    expect(roles).toEqual(["assistant", "tool", "assistant"]);
    expect(messages[1]).toMatchObject({ role: "tool", name: "web_search", status: "done" });
  });
});
