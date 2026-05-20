import { beforeEach, describe, expect, test } from "vitest";

import { pickMetadata } from "../src/lib/messageMetadata";
import { useChatStore } from "../src/store/chatStore";

describe("loadSession metadata merge", () => {
  beforeEach(() => {
    useChatStore.getState().reset();
  });

  test("preserves local metadata when server snapshot is still empty", () => {
    const assistantId = useChatStore.getState().beginUserTurn("hi");
    useChatStore.getState().setAssistantRunId(assistantId, "run_old");
    useChatStore.getState().appendAssistantMetadata(assistantId, {
      promptTokens: 10,
      completionTokens: 20,
      totalTokens: 30,
      durationMs: 1000,
      costUsd: 0.01,
    });
    useChatStore.getState().finishTurn(assistantId);

    useChatStore.getState().loadSession({
      session_id: "session_1",
      thread_id: "thread_1",
      title: "t",
      run_ids: ["run_old"],
      transcript: [
        { role: "user", content: "hi" },
        { role: "assistant", content: "hello" },
      ],
      metadata_by_run: {},
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    });

    const assistant = useChatStore.getState().messages.find((m) => m.role === "assistant");
    expect(assistant?.role).toBe("assistant");
    if (assistant?.role === "assistant") {
      expect(assistant.metadata?.totalTokens).toBe(30);
      expect(assistant.runId).toBe("run_old");
    }
  });

  test("pickMetadata prefers server when present", () => {
    const server = { totalTokens: 100, completionTokens: 80, durationMs: 2000, costUsd: 0.1 };
    const local = { totalTokens: 30, completionTokens: 20, durationMs: 1000, costUsd: 0.01 };
    expect(pickMetadata(server, local)?.totalTokens).toBe(100);
  });
});
