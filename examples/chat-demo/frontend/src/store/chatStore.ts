import { create } from "zustand";
import type { SessionDetailView } from "../types/api";
import type { ParsedToolState } from "../lib/events";

export type ToolCallStatus = "running" | "done" | "failed";

export interface ToolChatMessage {
  id: string;
  role: "tool";
  toolCallId: string;
  name: string;
  status: ToolCallStatus;
  argsSummary?: string;
  args?: Record<string, unknown>;
  resultPreview?: string;
  risk?: string;
  durationMs?: number;
}

export type ChatMessage =
  | { id: string; role: "user"; content: string }
  | { id: string; role: "assistant"; content: string; pending?: boolean }
  | ToolChatMessage;

export interface PendingInterrupt {
  runId: string;
  interruptId: string;
  reason: string;
  title?: string;
  description?: string;
  proposedAction?: Record<string, unknown>;
  allowedActions: string[];
}

function createId(prefix: string): string {
  return `${prefix}_${Math.random().toString(16).slice(2, 10)}`;
}

function isChatRole(role: string): role is "user" | "assistant" {
  return role === "user" || role === "assistant";
}

function insertAfterAssistant(messages: ChatMessage[], assistantId: string, item: ChatMessage): ChatMessage[] {
  const index = messages.findIndex((message) => message.id === assistantId);
  if (index < 0) {
    return [...messages, item];
  }
  let insertAt = index + 1;
  while (insertAt < messages.length && messages[insertAt]?.role === "tool") {
    insertAt += 1;
  }
  return [...messages.slice(0, insertAt), item, ...messages.slice(insertAt)];
}

interface ChatState {
  messages: ChatMessage[];
  streaming: boolean;
  lastSeq: number;
  sessionId?: string;
  runId?: string;
  pendingInterrupt?: PendingInterrupt;
  tokenUsage?: { prompt?: number; completion?: number };
  beginUserTurn: (text: string) => string;
  appendDelta: (assistantId: string, text: string) => void;
  appendToolStarted: (assistantId: string, tool: ParsedToolState) => void;
  updateToolCompleted: (toolCallId: string, tool: ParsedToolState) => void;
  finishTurn: (assistantId: string) => void;
  setStreaming: (value: boolean) => void;
  setLastSeq: (seq: number) => void;
  setSessionId: (sessionId?: string) => void;
  setRunId: (runId?: string) => void;
  setPendingInterrupt: (interrupt?: PendingInterrupt) => void;
  setTokenUsage: (usage?: { prompt?: number; completion?: number }) => void;
  setMessages: (messages: ChatMessage[]) => void;
  loadSession: (detail: SessionDetailView) => void;
  reset: () => void;
}

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  streaming: false,
  lastSeq: 0,
  sessionId: undefined,
  runId: undefined,
  pendingInterrupt: undefined,
  tokenUsage: undefined,
  beginUserTurn: (text) => {
    const userId = createId("user");
    const assistantId = createId("assistant");
    set((state) => ({
      streaming: true,
      pendingInterrupt: undefined,
      messages: [
        ...state.messages,
        { id: userId, role: "user", content: text },
        { id: assistantId, role: "assistant", content: "", pending: true },
      ],
    }));
    return assistantId;
  },
  appendDelta: (assistantId, text) => {
    if (!text) {
      return;
    }
    set((state) => ({
      messages: state.messages.map((message) =>
        message.id === assistantId && message.role === "assistant"
          ? { ...message, content: `${message.content}${text}` }
          : message,
      ),
    }));
  },
  appendToolStarted: (assistantId, tool) =>
    set((state) => {
      if (state.messages.some((item) => item.role === "tool" && item.toolCallId === tool.toolCallId)) {
        return state;
      }
      const toolMessage: ToolChatMessage = {
        id: createId("tool"),
        role: "tool",
        toolCallId: tool.toolCallId,
        name: tool.name,
        status: tool.status,
        argsSummary: tool.argsSummary,
        args: tool.args,
        resultPreview: tool.resultPreview,
        risk: tool.risk,
        durationMs: tool.durationMs,
      };
      return { messages: insertAfterAssistant(state.messages, assistantId, toolMessage) };
    }),
  updateToolCompleted: (toolCallId, tool) =>
    set((state) => ({
      messages: state.messages.map((message) =>
        message.role === "tool" && message.toolCallId === toolCallId
          ? {
              ...message,
              status: tool.status,
              resultPreview: tool.resultPreview ?? message.resultPreview,
              durationMs: tool.durationMs ?? message.durationMs,
            }
          : message,
      ),
    })),
  finishTurn: (assistantId) => {
    set((state) => ({
      streaming: false,
      messages: state.messages.map((message) =>
        message.id === assistantId && message.role === "assistant"
          ? { ...message, pending: false }
          : message,
      ),
    }));
  },
  setStreaming: (value) => set({ streaming: value }),
  setLastSeq: (seq) => set({ lastSeq: seq }),
  setSessionId: (sessionId) => set({ sessionId }),
  setRunId: (runId) => set({ runId }),
  setPendingInterrupt: (pendingInterrupt) => set({ pendingInterrupt }),
  setTokenUsage: (tokenUsage) => set({ tokenUsage }),
  setMessages: (messages) => set({ messages }),
  loadSession: (detail) =>
    set({
      streaming: false,
      lastSeq: 0,
      sessionId: detail.session_id,
      runId: detail.run_ids.at(-1),
      pendingInterrupt: undefined,
      tokenUsage: undefined,
      messages: detail.transcript
        .filter((item): item is { role: "user" | "assistant"; content: string } =>
          isChatRole(item.role),
        )
        .map((item) => ({
          id: createId(item.role),
          role: item.role,
          content: item.content,
          pending: false,
        })),
    }),
  reset: () =>
    set({
      messages: [],
      streaming: false,
      lastSeq: 0,
      sessionId: undefined,
      runId: undefined,
      pendingInterrupt: undefined,
      tokenUsage: undefined,
    }),
}));
