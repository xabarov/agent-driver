import { describe, expect, it } from "vitest";

import {
  eventsToMessages,
  parseSteeringEvents,
  type RunStreamEvent,
} from "../src/lib/events";

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

  it("keeps denied tool calls visible for policy feedback", () => {
    const messages = eventsToMessages([
      ev("run_started", 1),
      ev("tool_call_started", 2, {
        tools: [
          {
            tool_name: "file_write",
            tool_call_id: "tc1",
            args: { path: "demo.txt" },
          },
        ],
      }),
      ev("tool_call_completed", 3, {
        tools: [
          {
            tool_name: "file_write",
            tool_call_id: "tc1",
            status: "denied",
            result_summary: "force planning requires an approved plan",
          },
        ],
      }),
      ev("run_completed", 4),
    ]);
    expect(messages[0]).toMatchObject({
      role: "tool",
      name: "file_write",
      status: "denied",
      resultPreview: "force planning requires an approved plan",
    });
  });

  it("uses assistant completed snapshots for replay recovery", () => {
    const messages = eventsToMessages([
      ev("assistant_message_started", 1),
      ev("token_delta", 2, { delta_text: "partial" }),
      ev("assistant_message_completed", 3, { content: "final answer" }),
      ev("run_completed", 4),
    ]);
    expect(messages).toHaveLength(1);
    expect(messages[0]).toMatchObject({ role: "assistant", content: "final answer" });
  });

  it("drops tombstoned partial assistant output", () => {
    const messages = eventsToMessages([
      ev("assistant_message_started", 1),
      ev("token_delta", 2, { delta_text: "partial" }),
      ev("assistant_message_tombstoned", 3, { reason: "stream_idle_timeout" }),
      ev("run_failed", 4, { reason: "model_error" }),
    ]);
    expect(messages).toEqual([]);
  });
});

describe("parseSteeringEvents", () => {
  it("extracts control queue lifecycle events for replay", () => {
    const events = [
      ev("run_started", 1),
      ev("command_queued", 2, {
        queue_id: "cmd_1",
        control_id: "ctrl_1",
        kind: "enqueue_user_message",
        priority: "next",
      }),
      ev("control_applied", 3, {
        queue_id: "cmd_1",
        control_id: "ctrl_1",
        kind: "enqueue_user_message",
        priority: "next",
      }),
    ];

    expect(parseSteeringEvents(events)).toEqual([
      {
        seq: 2,
        event: "command_queued",
        queueId: "cmd_1",
        controlId: "ctrl_1",
        kind: "enqueue_user_message",
        priority: "next",
      },
      {
        seq: 3,
        event: "control_applied",
        queueId: "cmd_1",
        controlId: "ctrl_1",
        kind: "enqueue_user_message",
        priority: "next",
      },
    ]);
  });
});
