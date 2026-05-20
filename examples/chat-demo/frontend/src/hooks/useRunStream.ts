import { useCallback, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { fetchInterrupt } from "../lib/api";
import {
  buildLastEventId,
  isInterruptEvent,
  isTerminalEvent,
  getTokenDeltaText,
  isTokenDelta,
  isToolCallCompleted,
  isToolCallStarted,
  parseToolStatesFromEvent,
  type RunStreamEvent,
} from "../lib/events";
import { invalidateSessions, sessionDetailQueryKey } from "../lib/sessions";
import { resumeRunStream, startChatStream } from "../lib/sse";
import { useChatStore } from "../store/chatStore";
import { useSettingsStore } from "../store/settingsStore";

interface RunStreamController {
  sendMessage: (message: string) => Promise<void>;
  resumeInterrupt: (payload: {
    action: string;
    editedToolArgs?: Record<string, unknown>;
    message?: string;
  }) => Promise<void>;
  stopStreaming: () => void;
}

function applyStreamEvent(
  event: RunStreamEvent,
  assistantId: string,
): void {
  const store = useChatStore.getState();
  if (event.seq <= store.lastSeq) {
    return;
  }
  store.setLastSeq(event.seq);
  if (event.event === "run_started") {
    store.setStreaming(true);
  }
  if (isTokenDelta(event)) {
    store.appendDelta(assistantId, getTokenDeltaText(event));
  }
  if (isToolCallStarted(event)) {
    for (const tool of parseToolStatesFromEvent(event)) {
      store.appendToolStarted(assistantId, tool);
    }
  }
  if (isToolCallCompleted(event)) {
    for (const tool of parseToolStatesFromEvent(event)) {
      store.updateToolCompleted(tool.toolCallId, tool);
    }
  }
  if (event.event === "llm_call_completed") {
    const usage = event.data.usage;
    if (usage && typeof usage === "object") {
      const prompt = (usage as Record<string, unknown>).prompt_tokens;
      const completion = (usage as Record<string, unknown>).completion_tokens;
      store.setTokenUsage({
        prompt: typeof prompt === "number" ? prompt : undefined,
        completion: typeof completion === "number" ? completion : undefined,
      });
    }
  }
  if (isInterruptEvent(event)) {
    store.setStreaming(false);
    store.finishTurn(assistantId);
    const runId = store.runId ?? event.run_id;
    if (runId) {
      void fetchInterrupt(runId)
        .then((interrupt) => {
          useChatStore.getState().setPendingInterrupt({
            runId,
            interruptId: interrupt.interrupt_id,
            reason: interrupt.reason,
            title: interrupt.title ?? undefined,
            description: interrupt.description ?? undefined,
            proposedAction: interrupt.proposed_action,
            allowedActions: interrupt.allowed_actions,
          });
        })
        .catch(() => {
          useChatStore.getState().setPendingInterrupt({
            runId,
            interruptId: "",
            reason: String(event.data.reason ?? "approval_required"),
            allowedActions: ["approve", "reject", "cancel"],
          });
        });
    }
  }
}

export function useRunStream(): RunStreamController {
  const queryClient = useQueryClient();
  const toolPreset = useSettingsStore((state) => state.toolPreset);
  const abortRef = useRef<AbortController | null>(null);
  const activeAssistantRef = useRef<string | null>(null);

  const invalidateAfterTerminal = useCallback(() => {
    const current = useChatStore.getState();
    void invalidateSessions(queryClient);
    if (current.sessionId) {
      void queryClient.invalidateQueries({
        queryKey: sessionDetailQueryKey(current.sessionId),
      });
    }
  }, [queryClient]);

  const stopStreaming = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    useChatStore.getState().setStreaming(false);
  }, []);

  const runStream = useCallback(
    async (runner: (assistantId: string, signal: AbortSignal) => Promise<void>) => {
      const assistantId = activeAssistantRef.current;
      if (!assistantId) {
        return;
      }
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        await runner(assistantId, controller.signal);
      } catch (_error) {
        useChatStore.getState().finishTurn(assistantId);
        useChatStore.getState().setStreaming(false);
      } finally {
        abortRef.current = null;
      }
    },
    [],
  );

  const sendMessage = useCallback(
    async (message: string) => {
      const trimmed = message.trim();
      if (!trimmed || useChatStore.getState().streaming) {
        return;
      }
      const assistantId = useChatStore.getState().beginUserTurn(trimmed);
      activeAssistantRef.current = assistantId;
      await runStream(async (activeId, signal) => {
        const state = useChatStore.getState();
        await startChatStream({
          message: trimmed,
          sessionId: state.sessionId,
          toolPreset,
          signal,
          lastEventId: buildLastEventId(state.runId, state.lastSeq),
          onMeta: (meta) => {
            if (meta.sessionId) {
              useChatStore.getState().setSessionId(meta.sessionId);
            }
            if (meta.runId) {
              useChatStore.getState().setRunId(meta.runId);
            }
          },
          onEvent: (event) => {
            applyStreamEvent(event, activeId);
            if (isTerminalEvent(event)) {
              useChatStore.getState().finishTurn(activeId);
              useChatStore.getState().setPendingInterrupt(undefined);
              invalidateAfterTerminal();
            }
          },
        });
      });
    },
    [invalidateAfterTerminal, runStream, toolPreset],
  );

  const resumeInterrupt = useCallback(
    async (payload: {
      action: string;
      editedToolArgs?: Record<string, unknown>;
      message?: string;
    }) => {
      const state = useChatStore.getState();
      const interrupt = state.pendingInterrupt;
      const assistantId = [...state.messages]
        .reverse()
        .find((item) => item.role === "assistant" && item.pending)?.id;
      if (!interrupt || !assistantId || !interrupt.interruptId) {
        return;
      }
      activeAssistantRef.current = assistantId;
      useChatStore.getState().setPendingInterrupt(undefined);
      useChatStore.getState().setStreaming(true);
      await runStream(async (activeId, signal) => {
        const current = useChatStore.getState();
        await resumeRunStream({
          runId: interrupt.runId,
          interruptId: interrupt.interruptId,
          action: payload.action,
          toolPreset,
          editedToolArgs: payload.editedToolArgs,
          message: payload.message,
          signal,
          lastEventId: buildLastEventId(current.runId ?? interrupt.runId, current.lastSeq),
          onEvent: (event) => {
            applyStreamEvent(event, activeId);
            if (isTerminalEvent(event)) {
              useChatStore.getState().finishTurn(activeId);
              invalidateAfterTerminal();
            }
          },
        });
      });
    },
    [invalidateAfterTerminal, runStream, toolPreset],
  );

  return { sendMessage, resumeInterrupt, stopStreaming };
}
