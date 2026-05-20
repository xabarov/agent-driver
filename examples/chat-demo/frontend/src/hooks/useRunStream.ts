import { useCallback, useRef } from "react";

import { isTerminalEvent, isTokenDelta, type RunStreamEvent } from "../lib/events";
import { startChatStream } from "../lib/sse";
import { useChatStore } from "../store/chatStore";

interface RunStreamController {
  sendMessage: (message: string) => Promise<void>;
  stopStreaming: () => void;
}

export function useRunStream(): RunStreamController {
  const beginUserTurn = useChatStore((state) => state.beginUserTurn);
  const appendDelta = useChatStore((state) => state.appendDelta);
  const finishTurn = useChatStore((state) => state.finishTurn);
  const setStreaming = useChatStore((state) => state.setStreaming);
  const setLastSeq = useChatStore((state) => state.setLastSeq);
  const setSessionId = useChatStore((state) => state.setSessionId);

  const abortRef = useRef<AbortController | null>(null);

  const stopStreaming = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
  }, [setStreaming]);

  const sendMessage = useCallback(
    async (message: string) => {
      const trimmed = message.trim();
      if (!trimmed || useChatStore.getState().streaming) {
        return;
      }

      const assistantId = beginUserTurn(trimmed);
      const controller = new AbortController();
      abortRef.current = controller;
      const state = useChatStore.getState();

      try {
        await startChatStream({
          message: trimmed,
          sessionId: state.sessionId,
          signal: controller.signal,
          lastEventId: undefined,
          onMeta: (meta) => {
            if (meta.sessionId) {
              setSessionId(meta.sessionId);
            }
          },
          onEvent: (event: RunStreamEvent) => {
            const current = useChatStore.getState();
            if (event.seq <= current.lastSeq) {
              return;
            }
            setLastSeq(event.seq);
            if (event.event === "run_started") {
              setStreaming(true);
            }
            if (isTokenDelta(event)) {
              appendDelta(assistantId, event.data.delta_text);
            }
            if (isTerminalEvent(event)) {
              finishTurn(assistantId);
              setStreaming(false);
            }
          },
        });
      } catch (_error) {
        finishTurn(assistantId);
        setStreaming(false);
      } finally {
        abortRef.current = null;
      }
    },
    [
      appendDelta,
      beginUserTurn,
      finishTurn,
      setLastSeq,
      setSessionId,
      setStreaming,
    ],
  );

  return { sendMessage, stopStreaming };
}
