import { create } from "zustand";
import {
  hasMetadataContent,
  mergeAssistantMetadata,
  normalizeMetadataFromApi,
  pickMetadata,
  type AssistantMessageMetadata,
  type LlmCompletedPatch,
} from "../lib/messageMetadata";
import type { ParsedToolState } from "../lib/events";
import type { PlanningSnapshot } from "../lib/planning";
import { stripTextFormToolCalls } from "../lib/stripToolCalls";
import type { SessionDetailView } from "../types/api";

const PLANNING_TOOL_NAMES = new Set(["todo_write", "planning_state_update"]);

export type { AssistantMessageMetadata };

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
  | {
      id: string;
      role: "assistant";
      content: string;
      pending?: boolean;
      runId?: string;
      metadata?: AssistantMessageMetadata;
      planningSnapshot?: PlanningSnapshot;
    }
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
  lastError?: string;
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
  appendAssistantMetadata: (assistantId: string, patch: LlmCompletedPatch) => void;
  setPlanningSnapshot: (assistantId: string, snapshot: PlanningSnapshot) => void;
  setAssistantRunId: (assistantId: string, runId: string) => void;
  setLastError: (message?: string) => void;
  setMessages: (messages: ChatMessage[]) => void;
  deleteMessage: (messageId: string) => void;
  prepareRetry: (assistantId: string) => { userText: string; newAssistantId: string } | null;
  loadSession: (detail: SessionDetailView) => void;
  reset: () => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
  messages: [],
  streaming: false,
  lastSeq: 0,
  sessionId: undefined,
  runId: undefined,
  pendingInterrupt: undefined,
  lastError: undefined,
  beginUserTurn: (text) => {
    const userId = createId("user");
    const assistantId = createId("assistant");
    set((state) => ({
      streaming: true,
      pendingInterrupt: undefined,
      lastError: undefined,
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
      messages: state.messages.map((message) => {
        if (message.id !== assistantId || message.role !== "assistant") {
          return message;
        }
        const merged = `${message.content}${text}`;
        return { ...message, content: stripTextFormToolCalls(merged) };
      }),
    }));
  },
  appendToolStarted: (assistantId, tool) =>
    set((state) => {
      if (PLANNING_TOOL_NAMES.has(tool.name)) {
        return state;
      }
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
      messages: state.messages.map((message) => {
        if (message.id !== assistantId || message.role !== "assistant") {
          return message;
        }
        const content = message.content ? stripTextFormToolCalls(message.content) : message.content;
        return { ...message, pending: false, content };
      }),
    }));
  },
  setStreaming: (value) => set({ streaming: value }),
  setLastSeq: (seq) => set({ lastSeq: seq }),
  setSessionId: (sessionId) => set({ sessionId }),
  setRunId: (runId) => set({ runId }),
  setPendingInterrupt: (pendingInterrupt) => set({ pendingInterrupt }),
  appendAssistantMetadata: (assistantId, patch) =>
    set((state) => ({
      messages: state.messages.map((message) => {
        if (message.id !== assistantId || message.role !== "assistant") {
          return message;
        }
        return {
          ...message,
          metadata: mergeAssistantMetadata(message.metadata, patch),
        };
      }),
    })),
  setPlanningSnapshot: (assistantId, snapshot) =>
    set((state) => ({
      messages: state.messages.map((message) =>
        message.id === assistantId && message.role === "assistant"
          ? { ...message, planningSnapshot: snapshot }
          : message,
      ),
    })),
  setAssistantRunId: (assistantId, runId) =>
    set((state) => ({
      messages: state.messages.map((message) =>
        message.id === assistantId && message.role === "assistant"
          ? { ...message, runId }
          : message,
      ),
    })),
  setLastError: (lastError) => set({ lastError }),
  setMessages: (messages) => set({ messages }),
  deleteMessage: (messageId) =>
    set((state) => {
      const index = state.messages.findIndex((message) => message.id === messageId);
      if (index < 0) {
        return state;
      }
      const target = state.messages[index];
      if (!target) {
        return state;
      }
      if (target.role === "user") {
        let end = index + 1;
        while (end < state.messages.length && state.messages[end]?.role !== "user") {
          end += 1;
        }
        return { messages: [...state.messages.slice(0, index), ...state.messages.slice(end)] };
      }
      if (target.role === "assistant") {
        let end = index + 1;
        while (end < state.messages.length && state.messages[end]?.role === "tool") {
          end += 1;
        }
        return { messages: [...state.messages.slice(0, index), ...state.messages.slice(end)] };
      }
      return { messages: state.messages.filter((message) => message.id !== messageId) };
    }),
  prepareRetry: (assistantId) => {
    const state = get();
    const index = state.messages.findIndex((message) => message.id === assistantId);
    if (index < 0) {
      return null;
    }
    const assistant = state.messages[index];
    if (!assistant || assistant.role !== "assistant" || assistant.pending) {
      return null;
    }
    let userIndex = index - 1;
    while (userIndex >= 0 && state.messages[userIndex]?.role !== "user") {
      userIndex -= 1;
    }
    const userMessage = userIndex >= 0 ? state.messages[userIndex] : undefined;
    if (!userMessage || userMessage.role !== "user") {
      return null;
    }
    const newAssistantId = createId("assistant");
    set({
      streaming: true,
      pendingInterrupt: undefined,
      lastError: undefined,
      messages: [
        ...state.messages.slice(0, index),
        { id: newAssistantId, role: "assistant", content: "", pending: true },
      ],
    });
    return { userText: userMessage.content, newAssistantId };
  },
  loadSession: (detail) => {
    const prior = get().messages;
    const priorAssistants = prior.filter(
      (message): message is Extract<ChatMessage, { role: "assistant" }> =>
        message.role === "assistant",
    );
    const priorByRunId = new Map<string, AssistantMessageMetadata>();
    for (const message of priorAssistants) {
      if (message.runId && message.metadata && hasMetadataContent(message.metadata)) {
        priorByRunId.set(message.runId, message.metadata);
      }
    }
    let assistantRunIndex = 0;
    const messages: ChatMessage[] = detail.transcript
      .filter((item): item is SessionDetailView["transcript"][number] => isChatRole(item.role))
      .map((item) => {
        if (item.role === "assistant") {
          const runId = detail.run_ids[assistantRunIndex];
          assistantRunIndex += 1;
          const fromRun =
            runId && detail.metadata_by_run
              ? normalizeMetadataFromApi(detail.metadata_by_run[runId])
              : undefined;
          const fromTranscript = normalizeMetadataFromApi(item.metadata ?? undefined);
          const serverMetadata = fromTranscript ?? fromRun;
          const localMetadata =
            (runId ? priorByRunId.get(runId) : undefined) ??
            priorAssistants[assistantRunIndex - 1]?.metadata;
          return {
            id: createId(item.role),
            role: "assistant" as const,
            content: item.content,
            pending: false,
            runId,
            metadata: pickMetadata(serverMetadata, localMetadata),
          };
        }
        return {
          id: createId(item.role),
          role: "user" as const,
          content: item.content,
        };
      });
    set({
      streaming: false,
      lastSeq: 0,
      sessionId: detail.session_id,
      runId: detail.run_ids.at(-1),
      pendingInterrupt: undefined,
      lastError: undefined,
      messages,
    });
  },
  reset: () =>
    set({
      messages: [],
      streaming: false,
      lastSeq: 0,
      sessionId: undefined,
      runId: undefined,
      pendingInterrupt: undefined,
      lastError: undefined,
    }),
}));
