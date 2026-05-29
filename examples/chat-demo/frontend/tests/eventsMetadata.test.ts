import { describe, expect, test } from "vitest";

import { eventsToMessages, type RunStreamEvent } from "../src/lib/events";

describe("eventsToMessages metadata", () => {
  test("attaches aggregated metadata from llm_call_completed events", () => {
    const events: RunStreamEvent<Record<string, unknown>>[] = [
      {
        schema_version: "1.0",
        stream_id: "run_1:1",
        run_id: "run_1",
        attempt_id: "att_1",
        seq: 1,
        event: "run_started",
        source: "runtime_event",
        data: {},
      },
      {
        schema_version: "1.0",
        stream_id: "run_1:2",
        run_id: "run_1",
        attempt_id: "att_1",
        seq: 2,
        event: "token_delta",
        source: "runtime_event",
        data: { delta_text: "Hello" },
      },
      {
        schema_version: "1.0",
        stream_id: "run_1:3",
        run_id: "run_1",
        attempt_id: "att_1",
        seq: 3,
        event: "llm_call_completed",
        source: "runtime_event",
        data: {
          duration_ms: 2000,
          usage: { input_tokens: 5, output_tokens: 10, total_tokens: 15 },
        },
      },
      {
        schema_version: "1.0",
        stream_id: "run_1:4",
        run_id: "run_1",
        attempt_id: "att_1",
        seq: 4,
        event: "run_completed",
        source: "runtime_event",
        data: {},
      },
    ];
    const messages = eventsToMessages(events);
    const assistant = messages.find((item) => item.role === "assistant");
    expect(assistant?.role).toBe("assistant");
    if (assistant?.role === "assistant") {
      expect(assistant.content).toBe("Hello");
      expect(assistant.metadata?.totalTokens).toBe(15);
      expect(assistant.metadata?.durationMs).toBe(2000);
    }
  });
});
