import { beforeEach, describe, expect, test } from "vitest";

import {
  parseSubagentLifecycleEvent,
  parseToolStatesFromEvent,
  type RunStreamEvent,
} from "../src/lib/events";
import type { PlanningSnapshot } from "../src/lib/planning";
import { useChatStore } from "../src/store/chatStore";

const sampleSnapshot: PlanningSnapshot = {
  todos: [{ id: "s1", content: "Do work", status: "in_progress" }],
  inProgressId: "s1",
  inProgressIndex: 0,
  completed: 0,
  total: 1,
  planTitle: "Do work",
};

describe("chatStore", () => {
  beforeEach(() => {
    useChatStore.getState().reset();
  });

  test("beginUserTurn creates user and pending assistant", () => {
    useChatStore.getState().setLastSeq(42);
    useChatStore.getState().setRunId("run_previous");
    const assistantId = useChatStore.getState().beginUserTurn("hello");
    const state = useChatStore.getState();

    expect(state.lastSeq).toBe(0);
    expect(state.runId).toBeUndefined();
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

  test("appendDelta hides streamed text-form tool calls across chunks", () => {
    const assistantId = useChatStore.getState().beginUserTurn("plan");
    useChatStore.getState().appendDelta(assistantId, "Before\n<tool_call>{");
    useChatStore.getState().appendDelta(assistantId, '"name":"todo_write"');
    useChatStore.getState().appendDelta(assistantId, "}</tool_call>\nAfter");
    useChatStore.getState().finishTurn(assistantId);

    const assistant = useChatStore
      .getState()
      .messages.find((item) => item.id === assistantId);
    expect(assistant?.role === "assistant" && assistant.content).toBe("Before\nAfter");
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

  test("tracks steering control lifecycle", () => {
    useChatStore.getState().addSteeringControl({
      queueId: "cmd_1",
      message: "be concise",
      status: "queued",
    });
    useChatStore.getState().updateSteeringControl("cmd_1", "applied");

    expect(useChatStore.getState().steeringControls).toEqual([
      {
        queueId: "cmd_1",
        message: "be concise",
        status: "applied",
      },
    ]);
  });

  test("reset clears steering controls", () => {
    useChatStore.getState().addSteeringControl({
      queueId: "cmd_1",
      message: "be concise",
      status: "queued",
    });
    useChatStore.getState().reset();

    expect(useChatStore.getState().steeringControls).toEqual([]);
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

  test("loadSession restores persisted steering controls for latest run", () => {
    useChatStore.getState().loadSession({
      session_id: "session_abc123",
      thread_id: "thread_abc123",
      title: "Session title",
      run_ids: ["run_1"],
      created_at: "2026-05-20T00:00:00Z",
      updated_at: "2026-05-20T00:01:00Z",
      transcript: [
        { role: "user", content: "hello" },
        { role: "assistant", content: "world" },
      ],
      metadata_by_run: {
        run_1: {
          steering_controls: [
            {
              queue_id: "cmd_1",
              kind: "enqueue_user_message",
              status: "cancelled",
              payload: { message: "persisted steering" },
            },
          ],
        },
      },
    });

    expect(useChatStore.getState().steeringControls).toEqual([
      {
        queueId: "cmd_1",
        message: "persisted steering",
        status: "cancelled",
      },
    ]);
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
    useChatStore.getState().setAssistantRunId(assistantId, "run_old");
    useChatStore.getState().finishTurn(assistantId);
    const prepared = useChatStore.getState().prepareRetry(assistantId);
    expect(prepared).toEqual({
      userText: "retry me",
      newAssistantId: expect.any(String),
      retryFromRunId: "run_old",
    });
    const messages = useChatStore.getState().messages;
    expect(messages).toHaveLength(2);
    expect(messages[1]).toMatchObject({ role: "assistant", content: "", pending: true });
    expect(useChatStore.getState().lastSeq).toBe(0);
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

  test("tombstone preserves planning snapshot instead of dropping the plan", () => {
    const assistantId = useChatStore.getState().beginUserTurn("plan");
    useChatStore.getState().appendDelta(assistantId, "partial text");
    useChatStore.getState().setPlanningSnapshot(assistantId, sampleSnapshot);
    useChatStore.getState().tombstoneAssistant(assistantId);

    const assistant = useChatStore.getState().messages.find((item) => item.id === assistantId);
    expect(assistant?.role).toBe("assistant");
    if (assistant?.role === "assistant") {
      expect(assistant.content).toBe("");
      expect(assistant.pending).toBe(false);
      expect(assistant.planningSnapshot?.total).toBe(1);
    }
  });

  test("tombstone preserves terminal tool outcomes but removes running tools", () => {
    const assistantId = useChatStore.getState().beginUserTurn("tool policy");
    useChatStore.getState().appendToolStarted(assistantId, {
      toolCallId: "denied_1",
      name: "file_write",
      status: "running",
    });
    useChatStore.getState().appendToolStarted(assistantId, {
      toolCallId: "running_1",
      name: "web_search",
      status: "running",
    });
    useChatStore.getState().updateToolCompleted("denied_1", {
      toolCallId: "denied_1",
      name: "file_write",
      status: "denied",
      resultPreview: "force planning requires an approved plan",
    });
    useChatStore.getState().tombstoneAssistant(assistantId);

    const messages = useChatStore.getState().messages;
    expect(messages.some((item) => item.id === assistantId)).toBe(false);
    expect(messages).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          role: "tool",
          name: "file_write",
          status: "denied",
          resultPreview: "force planning requires an approved plan",
        }),
      ]),
    );
    expect(
      messages.some(
        (item) => item.role === "tool" && item.toolCallId === "running_1",
      ),
    ).toBe(false);
  });

  test("appendDelta recreates assistant output after tombstone", () => {
    const assistantId = useChatStore.getState().beginUserTurn("tool policy");
    useChatStore.getState().appendToolStarted(assistantId, {
      toolCallId: "denied_1",
      name: "file_write",
      status: "running",
    });
    useChatStore.getState().updateToolCompleted("denied_1", {
      toolCallId: "denied_1",
      name: "file_write",
      status: "denied",
    });
    useChatStore.getState().tombstoneAssistant(assistantId);
    useChatStore.getState().appendDelta(assistantId, "Final answer after denial.");
    useChatStore.getState().finishTurn(assistantId);

    expect(useChatStore.getState().messages).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ role: "tool", status: "denied" }),
        expect.objectContaining({
          id: assistantId,
          role: "assistant",
          content: "Final answer after denial.",
          pending: false,
        }),
      ]),
    );
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

  test("appendToolStarted skips ask_user_question control tool", () => {
    const assistantId = useChatStore.getState().beginUserTurn("clarify");
    useChatStore.getState().appendToolStarted(assistantId, {
      toolCallId: "ask1",
      name: "ask_user_question",
      status: "running",
    });

    const tools = useChatStore.getState().messages.filter((item) => item.role === "tool");
    expect(tools).toHaveLength(0);
  });

  test("appendToolStarted inserts tool card after assistant", () => {
    const assistantId = useChatStore.getState().beginUserTurn("run tool");
    useChatStore.getState().appendToolStarted(assistantId, {
      toolCallId: "call_1",
      name: "web_search",
      status: "running",
      argsSummary: "query: test",
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

  test("applySubagentLifecycle attaches child status to latest agent tool", () => {
    const assistantId = useChatStore.getState().beginUserTurn("delegate");
    useChatStore.getState().appendToolStarted(assistantId, {
      toolCallId: "agent_1",
      name: "agent_tool",
      status: "running",
      args: {
        description: "Verify facts",
        task: "Check facts and summarize.",
      },
    });
    useChatStore.getState().applySubagentLifecycle(assistantId, {
      seq: 2,
      event: "subagent_group_started",
      groupId: "group_1",
    });
    useChatStore.getState().applySubagentLifecycle(assistantId, {
      seq: 3,
      event: "subagent_completed",
      groupId: "group_1",
      childRun: {
        taskId: "task_1",
        childRunId: "run_child",
        status: "completed",
        description: "Verifier",
        outputPreview: "facts ok",
      },
    });
    const tool = useChatStore
      .getState()
      .messages.find((item) => item.role === "tool" && item.name === "agent_tool");

    expect(tool?.role).toBe("tool");
    if (tool?.role === "tool") {
      expect(tool.subagent?.groupStatus).toBe("running");
      expect(tool.subagent?.childRuns?.[0]).toMatchObject({
        taskId: "task_1",
        childRunId: "run_child",
        status: "completed",
        outputPreview: "facts ok",
      });
    }
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
    expect(tools[0].argsSummary).toBe("path: README.md");
  });

  test("parses subagent lifecycle events", () => {
    const event: RunStreamEvent<Record<string, unknown>> = {
      schema_version: "1.0",
      stream_id: "run_1:2",
      run_id: "run_1",
      attempt_id: "att_1",
      seq: 2,
      event: "subagent_completed",
      source: "runtime_event",
      data: {
        group_id: "group_1",
        task_id: "task_1",
        child_run_id: "run_child",
        status: "completed",
        description: "Verifier",
        summary: "facts ok",
        used_tools: ["web_search"],
        warning: "low confidence",
      },
    };

    expect(parseSubagentLifecycleEvent(event)).toMatchObject({
      event: "subagent_completed",
      groupId: "group_1",
      childRun: {
        taskId: "task_1",
        childRunId: "run_child",
        status: "completed",
        description: "Verifier",
        outputPreview: "facts ok",
        usedTools: ["web_search"],
        warning: "low confidence",
      },
    });
  });
});
