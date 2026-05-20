import { beforeEach, describe, expect, test } from "vitest";

import { parseToolStatesFromEvent, type RunStreamEvent } from "../src/lib/events";
import { useChatStore } from "../src/store/chatStore";

describe("chatStore", () => {
  beforeEach(() => {
    useChatStore.getState().reset();
  });

  test("beginUserTurn creates user and pending assistant", () => {
    const assistantId = useChatStore.getState().beginUserTurn("hello");
    const state = useChatStore.getState();

    expect(state.messages).toHaveLength(2);
    expect(state.messages[0]).toMatchObject({ role: "user", content: "hello" });
    expect(state.messages[1]).toMatchObject({
      id: assistantId,
      role: "assistant",
      content: "",
      pending: true,
    });
  });

  test("appendDelta concatenates tokens in assistant message", () => {
    const assistantId = useChatStore.getState().beginUserTurn("hi");
    useChatStore.getState().appendDelta(assistantId, "hel");
    useChatStore.getState().appendDelta(assistantId, "lo");
    useChatStore.getState().appendDelta(assistantId, " world");

    const assistant = useChatStore
      .getState()
      .messages.find((item) => item.id === assistantId);
    expect(assistant?.role === "assistant" && assistant.content).toBe("hello world");
  });

  test("finishTurn clears pending and streaming", () => {
    const assistantId = useChatStore.getState().beginUserTurn("hi");
    useChatStore.getState().appendDelta(assistantId, "ok");
    useChatStore.getState().finishTurn(assistantId);

    const state = useChatStore.getState();
    const assistant = state.messages.find((item) => item.id === assistantId);
    expect(state.streaming).toBe(false);
    expect(assistant?.role === "assistant" && assistant.pending).toBe(false);
  });

  test("loadSession replaces messages and resets stream state", () => {
    const state = useChatStore.getState();
    state.beginUserTurn("stale");
    state.setLastSeq(123);
    state.setStreaming(true);

    state.loadSession({
      session_id: "session_abc123",
      thread_id: "thread_abc123",
      title: "Session title",
      run_ids: ["run_1"],
      created_at: "2026-05-20T00:00:00Z",
      updated_at: "2026-05-20T00:01:00Z",
      transcript: [
        { role: "system", content: "internal metadata" },
        { role: "user", content: "hello" },
        { role: "assistant", content: "world" },
      ],
    });

    const loaded = useChatStore.getState();
    expect(loaded.sessionId).toBe("session_abc123");
    expect(loaded.streaming).toBe(false);
    expect(loaded.lastSeq).toBe(0);
    expect(loaded.messages).toHaveLength(2);
    expect(loaded.messages[0]).toMatchObject({ role: "user", content: "hello", pending: false });
    expect(loaded.messages[1]).toMatchObject({
      role: "assistant",
      content: "world",
      pending: false,
    });
  });

  test("appendToolStarted inserts tool card after assistant", () => {
    const assistantId = useChatStore.getState().beginUserTurn("run tool");
    useChatStore.getState().appendToolStarted(assistantId, {
      toolCallId: "call_1",
      name: "web_search",
      status: "running",
      argsSummary: '{"query":"test"}',
    });
    const messages = useChatStore.getState().messages;
    expect(messages).toHaveLength(3);
    expect(messages[2]).toMatchObject({
      role: "tool",
      toolCallId: "call_1",
      name: "web_search",
      status: "running",
    });
  });
});

describe("parseToolStatesFromEvent", () => {
  test("parses tools array from tool_call_started", () => {
    const event: RunStreamEvent<Record<string, unknown>> = {
      schema_version: "1.0",
      stream_id: "run_1:1",
      run_id: "run_1",
      attempt_id: "att_1",
      seq: 1,
      event: "tool_call_started",
      source: "runtime_event",
      data: {
        tools: [
          {
            tool_name: "read_file",
            tool_call_id: "call_a",
            args: { path: "README.md" },
          },
        ],
      },
    };
    const tools = parseToolStatesFromEvent(event);
    expect(tools).toHaveLength(1);
    expect(tools[0]).toMatchObject({ name: "read_file", toolCallId: "call_a", status: "running" });
  });
});
