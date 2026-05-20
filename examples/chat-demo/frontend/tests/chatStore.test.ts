import { beforeEach, describe, expect, test } from "vitest";

import { parseToolStatesFromEvent, type RunStreamEvent } from "../src/lib/events";
import type { PlanningSnapshot } from "../src/lib/planning";
import { useChatStore } from "../src/store/chatStore";

const sampleSnapshot: PlanningSnapshot = {
  todos: [{ id: "s1", content: "Do work", status: "in_progress" }],
  inProgressId: "s1",
  completed: 0,
  total: 1,
  planTitle: "Do work",
};

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
    expect(loaded.messages[0]).toMatchObject({ role: "user", content: "hello" });
    expect(loaded.messages[1]).toMatchObject({
      role: "assistant",
      content: "world",
      pending: false,
    });
  });

  test("deleteMessage removes assistant turn including tools", () => {
    const assistantId = useChatStore.getState().beginUserTurn("hi");
    useChatStore.getState().appendToolStarted(assistantId, {
      toolCallId: "call_1",
      name: "web_search",
      status: "running",
    });
    useChatStore.getState().finishTurn(assistantId);
    const assistant = useChatStore.getState().messages[1];
    expect(assistant?.role).toBe("assistant");
    if (assistant?.role === "assistant") {
      useChatStore.getState().deleteMessage(assistant.id);
    }
    expect(useChatStore.getState().messages).toHaveLength(1);
    expect(useChatStore.getState().messages[0]).toMatchObject({ role: "user", content: "hi" });
  });

  test("appendAssistantMetadata merges usage across LLM steps", () => {
    const assistantId = useChatStore.getState().beginUserTurn("meta");
    useChatStore.getState().appendAssistantMetadata(assistantId, {
      promptTokens: 10,
      completionTokens: 20,
      totalTokens: 30,
      durationMs: 1000,
      costUsd: 0.01,
    });
    useChatStore.getState().appendAssistantMetadata(assistantId, {
      promptTokens: 5,
      completionTokens: 15,
      totalTokens: 20,
      durationMs: 500,
      costUsd: 0.002,
    });
    const assistant = useChatStore.getState().messages.find((item) => item.id === assistantId);
    expect(assistant?.role).toBe("assistant");
    if (assistant?.role === "assistant") {
      expect(assistant.metadata?.promptTokens).toBe(15);
      expect(assistant.metadata?.completionTokens).toBe(35);
      expect(assistant.metadata?.durationMs).toBe(1500);
      expect(assistant.metadata?.costUsd).toBeCloseTo(0.012);
    }
  });

  test("prepareRetry replaces assistant and returns user text", () => {
    const assistantId = useChatStore.getState().beginUserTurn("retry me");
    useChatStore.getState().appendDelta(assistantId, "old answer");
    useChatStore.getState().finishTurn(assistantId);
    const prepared = useChatStore.getState().prepareRetry(assistantId);
    expect(prepared).toEqual({ userText: "retry me", newAssistantId: expect.any(String) });
    const messages = useChatStore.getState().messages;
    expect(messages).toHaveLength(2);
    expect(messages[1]).toMatchObject({ role: "assistant", content: "", pending: true });
  });

  test("setPlanningSnapshot updates assistant in place", () => {
    const assistantId = useChatStore.getState().beginUserTurn("plan");
    useChatStore.getState().setPlanningSnapshot(assistantId, sampleSnapshot);
    const assistant = useChatStore.getState().messages.find((item) => item.id === assistantId);
    expect(assistant?.role).toBe("assistant");
    if (assistant?.role === "assistant") {
      expect(assistant.planningSnapshot?.total).toBe(1);
    }
  });

  test("appendToolStarted skips todo_write", () => {
    const assistantId = useChatStore.getState().beginUserTurn("plan");
    useChatStore.getState().appendToolStarted(assistantId, {
      toolCallId: "tw1",
      name: "todo_write",
      status: "running",
    });
    useChatStore.getState().appendToolStarted(assistantId, {
      toolCallId: "fw1",
      name: "file_write",
      status: "running",
    });
    const tools = useChatStore.getState().messages.filter((item) => item.role === "tool");
    expect(tools).toHaveLength(1);
    expect(tools[0]).toMatchObject({ name: "file_write" });
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
