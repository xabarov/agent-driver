import { beforeEach, describe, expect, test } from "vitest";

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
    expect(assistant?.content).toBe("hello world");
  });

  test("finishTurn clears pending and streaming", () => {
    const assistantId = useChatStore.getState().beginUserTurn("hi");
    useChatStore.getState().appendDelta(assistantId, "ok");
    useChatStore.getState().finishTurn(assistantId);

    const state = useChatStore.getState();
    const assistant = state.messages.find((item) => item.id === assistantId);
    expect(state.streaming).toBe(false);
    expect(assistant?.pending).toBe(false);
  });
});
