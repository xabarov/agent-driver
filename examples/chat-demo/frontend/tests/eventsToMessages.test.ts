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

interface RuntimeTimelineRow {
  seq: number;
  category:
    | "assistant"
    | "tool"
    | "source"
    | "artifact"
    | "lifecycle"
    | "warning";
  state: "started" | "delta" | "completed" | "failed";
  item_id?: string;
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

  it("keeps replay source evidence on tool messages", () => {
    const messages = eventsToMessages([
      ev("run_started", 1),
      ev("tool_call_started", 2, {
        tools: [
          {
            tool_name: "web_fetch",
            tool_call_id: "tc1",
            args: { url: "https://example.com/source" },
          },
        ],
      }),
      ev("tool_call_completed", 3, {
        tools: [
          {
            tool_name: "web_fetch",
            tool_call_id: "tc1",
            status: "completed",
            sources: [
              {
                id: "web_fetch:tc1:1",
                url: "https://example.com/source",
                canonical_url: "https://example.com/source",
                source_type: "web_fetch",
                title: "Fetched source",
              },
            ],
          },
        ],
      }),
      ev("run_completed", 4),
    ]);

    expect(messages[0]).toMatchObject({
      role: "tool",
      name: "web_fetch",
      sources: [
        {
          sourceType: "web_fetch",
          title: "Fetched source",
          domain: "example.com",
        },
      ],
    });
  });

  it("does not attach source evidence from blocked fetch tool messages", () => {
    const messages = eventsToMessages([
      ev("run_started", 1),
      ev("tool_call_completed", 2, {
        tools: [
          {
            tool_name: "web_fetch",
            tool_call_id: "tc1",
            status: "completed",
            result_summary: "web_fetch blocked by upstream HTTP 403",
            sources: [
              {
                id: "web_fetch:tc1:1",
                url: "https://example.com/blocked",
                canonical_url: "https://example.com/blocked",
                source_type: "web_fetch",
                title: "Blocked source",
              },
            ],
          },
        ],
      }),
      ev("token_delta", 3, { delta_text: "No trusted source." }),
      ev("run_completed", 4),
    ]);

    expect(messages[0]).toMatchObject({
      role: "tool",
      name: "web_fetch",
      status: "done",
    });
    expect(messages[0].role).toBe("tool");
    if (messages[0].role === "tool") {
      expect(messages[0].sources).toBeUndefined();
    }
    const assistant = messages.find((message) => message.role === "assistant");
    expect(assistant?.sources).toBeUndefined();
  });

  it("attaches collected replay sources to the final assistant answer", () => {
    const messages = eventsToMessages([
      ev("run_started", 1),
      ev("tool_call_completed", 2, {
        tools: [
          {
            tool_name: "web_fetch",
            tool_call_id: "tc1",
            status: "completed",
            sources: [
              {
                id: "web_fetch:tc1:1",
                url: "https://example.com/source",
                canonical_url: "https://example.com/source",
                source_type: "web_fetch",
                title: "Fetched source",
              },
            ],
          },
        ],
      }),
      ev("token_delta", 3, { delta_text: "Final answer without explicit links." }),
      ev("run_completed", 4),
    ]);

    const assistant = messages.find((message) => message.role === "assistant");
    expect(assistant).toMatchObject({
      role: "assistant",
      sources: [
        {
          sourceType: "web_fetch",
          title: "Fetched source",
        },
      ],
    });
  });

  it("matches canonical runtime timeline rows for research tools and artifacts", () => {
    const events = [
      ev("run_started", 1),
      ev("tool_call_completed", 2, {
        tools: [
          {
            tool_name: "web_fetch",
            tool_call_id: "tc1",
            status: "completed",
            sources: [
              {
                id: "web_fetch:tc1:1",
                url: "https://example.com/source",
                canonical_url: "https://example.com/source",
                source_type: "web_fetch",
                title: "Fetched source",
              },
            ],
          },
        ],
      }),
      ev("source_ledger_updated", 3, {
        verified_reads: [
          {
            id: "web_fetch:tc1:1",
            url: "https://example.com/source",
            source_type: "web_fetch",
            title: "Fetched source",
          },
        ],
      }),
      ev("artifact_updated", 4, {
        path: "research/report.md",
        size_bytes: 1024,
      }),
      ev("token_delta", 5, { delta_text: "Final answer." }),
      ev("run_completed", 6, {
        deep_research_artifacts: {
          report_exists: true,
          report_path: "research/report.md",
          report_size_bytes: 1024,
        },
      }),
    ];
    const upstreamRows: RuntimeTimelineRow[] = [
      { seq: 1, category: "lifecycle", state: "started", item_id: "run_started" },
      { seq: 2, category: "tool", state: "completed", item_id: "tc1" },
      {
        seq: 3,
        category: "source",
        state: "completed",
        item_id: "source_ledger_updated",
      },
      {
        seq: 4,
        category: "artifact",
        state: "delta",
        item_id: "research/report.md",
      },
      { seq: 5, category: "assistant", state: "delta", item_id: "token_delta" },
      { seq: 6, category: "lifecycle", state: "completed", item_id: "run_completed" },
    ];

    const messages = eventsToMessages(events);
    const tool = messages.find((message) => message.role === "tool");
    const assistant = messages.find((message) => message.role === "assistant");

    expect(upstreamRows.map((row) => `${row.seq}:${row.category}:${row.state}`)).toEqual([
      "1:lifecycle:started",
      "2:tool:completed",
      "3:source:completed",
      "4:artifact:delta",
      "5:assistant:delta",
      "6:lifecycle:completed",
    ]);
    expect(tool).toMatchObject({
      role: "tool",
      name: "web_fetch",
      status: "done",
      sources: [{ title: "Fetched source" }],
    });
    expect(assistant).toMatchObject({
      role: "assistant",
      content: "Final answer.",
      sources: [{ title: "Fetched source" }],
      deepResearch: {
        artifact: {
          reportPath: "research/report.md",
          reportSizeBytes: 1024,
        },
      },
    });
    expect(upstreamRows.at(-1)).toMatchObject({
      category: "lifecycle",
      state: "completed",
    });
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

  it("does not render text-form tool call json as assistant content", () => {
    const messages = eventsToMessages([
      ev("run_started", 1),
      ev("token_delta", 2, {
        delta_text:
          '{"name":"todo_update","arguments":{"todo_id":"research","status":"in_progress"}} </tool_call>',
      }),
      ev("run_completed", 3),
    ]);
    expect(messages).toEqual([]);
  });

  it("renders compaction lifecycle once and updates its status", () => {
    const messages = eventsToMessages([
      ev("run_started", 1),
      ev("memory_compaction_started", 2, {
        compaction_id: "compact_1",
        mode: "partial",
        reason: "token_pressure",
      }),
      ev("memory_compacted", 3, {
        compaction_id: "compact_1",
        mode: "partial",
        outcome: "success",
        summarized_message_count: 8,
      }),
      ev("token_delta", 4, { delta_text: "Done" }),
      ev("run_completed", 5),
    ]);

    expect(messages).toHaveLength(2);
    expect(messages[0]).toMatchObject({
      role: "compaction",
      compactionId: "compact_1",
      status: "done",
      summarizedMessageCount: 8,
    });
    expect(messages[1]).toMatchObject({ role: "assistant", content: "Done" });
  });

  it("hides skipped compactions in normal replay messages", () => {
    const messages = eventsToMessages([
      ev("run_started", 1),
      ev("memory_compacted", 2, {
        compaction_id: "compact_1",
        outcome: "skipped",
      }),
      ev("token_delta", 3, { delta_text: "No compaction needed" }),
      ev("run_completed", 4),
    ]);

    expect(messages).toHaveLength(1);
    expect(messages[0]).toMatchObject({
      role: "assistant",
      content: "No compaction needed",
    });
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
