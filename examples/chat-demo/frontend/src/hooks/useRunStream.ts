import { useCallback, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { cancelRun, controlRun, fetchInterrupt } from "../lib/api";
import {
  buildLastEventId,
  getAssistantSnapshotContent,
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
import { parseLlmCompletedData } from "../lib/messageMetadata";
import { parsePlanningSnapshot } from "../lib/planning";
import { formatStreamError } from "../lib/streamError";
import { useChatStore } from "../store/chatStore";
import { normalizeToolPreset, useSettingsStore } from "../store/settingsStore";

interface RunStreamController {
  sendMessage: (message: string) => Promise<void>;
  steerRun: (message: string) => Promise<void>;
  retryAssistant: (assistantId: string) => Promise<void>;
  resumeInterrupt: (payload: {
    action: string;
    editedToolArgs?: Record<string, unknown>;
    message?: string;
  }) => Promise<void>;
  stopStreaming: () => void;
}

function createClientRequestId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `req_${crypto.randomUUID()}`;
  }
  return `req_${Date.now().toString(16)}_${Math.random().toString(16).slice(2)}`;
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
  if (event.event === "assistant_message_completed" || event.event === "assistant_message_replaced") {
    const content = getAssistantSnapshotContent(event);
    if (content !== undefined) {
      store.replaceAssistantContent(assistantId, content);
    }
  }
  if (event.event === "assistant_message_tombstoned") {
    store.tombstoneAssistant(assistantId);
  }
  if (isToolCallStarted(event)) {
    for (const tool of parseToolStatesFromEvent(event)) {
      store.appendToolStarted(assistantId, tool);
    }
  }
  if (isToolCallCompleted(event)) {
    const snapshot = parsePlanningSnapshot(event.data.planning_snapshot);
    if (snapshot) {
      store.setPlanningSnapshot(assistantId, snapshot);
    }
    for (const tool of parseToolStatesFromEvent(event)) {
      store.updateToolCompleted(tool.toolCallId, tool);
    }
  }
  if (event.event === "llm_call_completed" || event.event === "run_completed") {
    const snapshot = parsePlanningSnapshot(event.data.planning_snapshot);
    if (snapshot) {
      store.setPlanningSnapshot(assistantId, snapshot);
    }
  }
  if (event.event === "llm_call_completed") {
    const patch = parseLlmCompletedData(event.data);
    if (Object.keys(patch).length > 0) {
      store.appendAssistantMetadata(assistantId, patch);
    }
  }
  if (isTerminalEvent(event) && event.data.usage && typeof event.data.usage === "object") {
    const patch = parseLlmCompletedData({ usage: event.data.usage as Record<string, unknown> });
    if (Object.keys(patch).length > 0) {
      store.appendAssistantMetadata(assistantId, patch);
    }
  }
  if (event.event === "run_failed") {
    const reason =
      typeof event.data.error === "string"
        ? event.data.error
        : typeof event.data.message === "string"
          ? event.data.message
          : "Run failed";
    store.setLastError(reason);
    store.setStreaming(false);
    store.finishTurn(assistantId);
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
  const toolPreset = normalizeToolPreset(useSettingsStore((state) => state.toolPreset));
  const model = useSettingsStore((state) => state.model);
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
    const store = useChatStore.getState();
    const runId = store.runId;
    const assistantId = activeAssistantRef.current;
    abortRef.current?.abort();
    abortRef.current = null;
    if (runId) {
      void cancelRun(runId).catch(() => undefined);
    }
    if (assistantId) {
      store.finishTurn(assistantId);
    }
    store.setStreaming(false);
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
      } catch (error) {
        const store = useChatStore.getState();
        store.setLastError(formatStreamError(error));
        store.finishTurn(assistantId);
        store.setStreaming(false);
      } finally {
        abortRef.current = null;
      }
    },
    [],
  );

  const streamUserMessage = useCallback(
    async (trimmed: string, assistantId: string, retryFromRunId?: string) => {
      activeAssistantRef.current = assistantId;
      const clientRequestId = createClientRequestId();
      await runStream(async (activeId, signal) => {
        const state = useChatStore.getState();
        await startChatStream({
          message: trimmed,
          sessionId: state.sessionId,
          toolPreset,
          model: model || undefined,
          retryFromRunId,
          clientRequestId,
          signal,
          lastEventId: retryFromRunId ? undefined : buildLastEventId(state.runId, state.lastSeq),
          onMeta: (meta) => {
            const store = useChatStore.getState();
            if (meta.sessionId) {
              store.setSessionId(meta.sessionId);
            }
            if (meta.runId) {
              store.setRunId(meta.runId);
              store.setAssistantRunId(activeId, meta.runId);
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
    [invalidateAfterTerminal, model, runStream, toolPreset],
  );

  const sendMessage = useCallback(
    async (message: string) => {
      const trimmed = message.trim();
      if (!trimmed || useChatStore.getState().streaming) {
        return;
      }
      const assistantId = useChatStore.getState().beginUserTurn(trimmed);
      await streamUserMessage(trimmed, assistantId);
    },
    [streamUserMessage],
  );

  const steerRun = useCallback(async (message: string) => {
    const trimmed = message.trim();
    const runId = useChatStore.getState().runId;
    if (!trimmed || !runId) {
      return;
    }
    try {
      await controlRun(runId, {
        kind: "enqueue_user_message",
        priority: "next",
        payload: { message: trimmed },
      });
    } catch (error) {
      useChatStore.getState().setLastError(formatStreamError(error));
    }
  }, []);

  const retryAssistant = useCallback(
    async (assistantId: string) => {
      if (useChatStore.getState().streaming) {
        return;
      }
      const prepared = useChatStore.getState().prepareRetry(assistantId);
      if (!prepared) {
        return;
      }
      await streamUserMessage(
        prepared.userText,
        prepared.newAssistantId,
        prepared.retryFromRunId,
      );
    },
    [streamUserMessage],
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
          model: model || undefined,
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
    [invalidateAfterTerminal, model, runStream, toolPreset],
  );

  return { sendMessage, steerRun, retryAssistant, resumeInterrupt, stopStreaming };
}
