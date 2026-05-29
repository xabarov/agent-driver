import { useEffect } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { useSession } from "../../lib/sessions";
import { useRunStream } from "../../hooks/useRunStream";
import { useChatStore } from "../../store/chatStore";
import { ChatComposer } from "./ChatComposer";
import { EmptyState } from "./EmptyState";
import { FakeProviderBanner } from "./FakeProviderBanner";
import { InterruptCard } from "./InterruptCard";
import { MessageList } from "./MessageList";
import { SessionRunsMenu } from "./SessionRunsMenu";
import { StreamErrorBanner } from "./StreamErrorBanner";

interface ChatPageProps {
  mode: "new" | "existing";
}

export function ChatPage({ mode }: ChatPageProps) {
  const navigate = useNavigate();
  const params = useParams<{ id: string }>();
  const sessionId = useChatStore((state) => state.sessionId);
  const pendingInterrupt = useChatStore((state) => state.pendingInterrupt);
  const reset = useChatStore((state) => state.reset);
  const loadSession = useChatStore((state) => state.loadSession);
  const messages = useChatStore((state) => state.messages);
  const streaming = useChatStore((state) => state.streaming);
  const steeringControls = useChatStore((state) => state.steeringControls);
  const {
    sendMessage,
    steerRun,
    cancelSteering,
    retryAssistant,
    resumeInterrupt,
    stopStreaming,
  } = useRunStream();
  const selectedSessionId = mode === "existing" ? params.id ?? "" : "";
  const sessionQuery = useSession(selectedSessionId);

  useEffect(() => {
    if (mode === "new") {
      stopStreaming();
      reset();
    }
  }, [mode, reset, stopStreaming]);

  useEffect(() => {
    if (mode === "new" && sessionId) {
      navigate(`/sessions/${sessionId}`, { replace: true });
    }
  }, [mode, navigate, sessionId]);

  useEffect(() => {
    if (mode !== "existing" || !sessionQuery.data) {
      return;
    }
    const detail = sessionQuery.data;
    const store = useChatStore.getState();
    // After first SSE meta we navigate here; reloading would stopStreaming + wipe pending assistant.
    if (store.streaming && store.sessionId === detail.session_id) {
      return;
    }
    // Interrupt metadata arrives right before the session query invalidates; keep
    // the approval card mounted instead of reloading the transcript over it.
    if (store.pendingInterrupt && store.sessionId === detail.session_id) {
      return;
    }
    if (store.streaming) {
      stopStreaming();
    }
    loadSession(detail);
  }, [loadSession, mode, pendingInterrupt, sessionQuery.data, stopStreaming]);

  if (mode === "existing" && sessionQuery.isLoading) {
    return (
      <div className="flex flex-1 items-center justify-center p-4 text-sm text-muted-foreground">
        Loading session…
      </div>
    );
  }

  if (mode === "existing" && sessionQuery.isError) {
    return (
      <div className="flex flex-1 items-center justify-center p-4 text-sm text-destructive">
        Failed to load session.
      </div>
    );
  }

  const blocked = Boolean(pendingInterrupt);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="mx-auto flex w-full min-h-0 max-w-3xl flex-1 flex-col px-4 pt-3 lg:max-w-4xl xl:max-w-5xl">
        {mode === "existing" && sessionQuery.data && sessionQuery.data.run_ids.length > 0 ? (
          <div className="mb-2 flex justify-end">
            <SessionRunsMenu
              sessionId={selectedSessionId}
              runIds={sessionQuery.data.run_ids}
            />
          </div>
        ) : null}
        <div className="mb-2 space-y-2">
          <FakeProviderBanner />
          <StreamErrorBanner />
        </div>
        <div className="min-h-0 flex-1">
          {messages.length === 0 ? (
            <EmptyState
              onPromptSelect={(text) => {
                void sendMessage(text);
              }}
            />
          ) : (
            <MessageList
              messages={messages}
              onRetryAssistant={(assistantId) => {
                void retryAssistant(assistantId);
              }}
            />
          )}
        </div>
        {pendingInterrupt ? (
          <div className="mb-3 shrink-0">
            <InterruptCard
              interrupt={pendingInterrupt}
              onAction={(payload) => {
                void resumeInterrupt(payload);
              }}
            />
          </div>
        ) : null}
      </div>
      <ChatComposer
        streaming={streaming}
        disabled={blocked}
        steeringControls={steeringControls}
        onSend={(text) => {
          void sendMessage(text);
        }}
        onSteer={(text) => {
          void steerRun(text);
        }}
        onCancelSteering={(queueId) => {
          void cancelSteering(queueId);
        }}
        onStop={stopStreaming}
      />
    </div>
  );
}
