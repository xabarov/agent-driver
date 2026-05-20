import { create } from "zustand";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  pending?: boolean;
}

function createId(prefix: string): string {
  return `${prefix}_${Math.random().toString(16).slice(2, 10)}`;
}

interface ChatState {
  messages: ChatMessage[];
  streaming: boolean;
  lastSeq: number;
  sessionId?: string;
  beginUserTurn: (text: string) => string;
  appendDelta: (assistantId: string, text: string) => void;
  finishTurn: (assistantId: string) => void;
  setStreaming: (value: boolean) => void;
  setLastSeq: (seq: number) => void;
  setSessionId: (sessionId?: string) => void;
  reset: () => void;
}

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  streaming: false,
  lastSeq: 0,
  sessionId: undefined,
  beginUserTurn: (text) => {
    const userId = createId("user");
    const assistantId = createId("assistant");
    set((state) => ({
      streaming: true,
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
        message.id === assistantId
          ? { ...message, content: `${message.content}${text}` }
          : message,
      ),
    }));
  },
  finishTurn: (assistantId) => {
    set((state) => ({
      streaming: false,
      messages: state.messages.map((message) =>
        message.id === assistantId ? { ...message, pending: false } : message,
      ),
    }));
  },
  setStreaming: (value) => set({ streaming: value }),
  setLastSeq: (seq) => set({ lastSeq: seq }),
  setSessionId: (sessionId) => set({ sessionId }),
  reset: () => set({ messages: [], streaming: false, lastSeq: 0, sessionId: undefined }),
}));
