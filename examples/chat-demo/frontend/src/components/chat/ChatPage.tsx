import { useEffect } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { useSession } from "../../lib/sessions";
import { useRunStream } from "../../hooks/useRunStream";
import { useChatStore } from "../../store/chatStore";
import { Card, CardContent } from "../ui/card";
import { EmptyState } from "./EmptyState";
import { ComposerInput } from "./ComposerInput";
import { InterruptCard } from "./InterruptCard";
import { MessageList } from "./MessageList";

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
  const { sendMessage, resumeInterrupt, stopStreaming } = useRunStream();
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
    if (mode === "existing" && sessionQuery.data) {
      stopStreaming();
      loadSession(sessionQuery.data);
    }
  }, [loadSession, mode, sessionQuery.data, stopStreaming]);

  if (mode === "existing" && sessionQuery.isLoading) {
    return (
      <Card className="h-full">
        <CardContent className="p-4 text-sm text-muted-foreground">Loading session...</CardContent>
      </Card>
    );
  }

  if (mode === "existing" && sessionQuery.isError) {
    return (
      <Card className="h-full">
        <CardContent className="p-4 text-sm text-destructive">Failed to load session.</CardContent>
      </Card>
    );
  }

  const blocked = Boolean(pendingInterrupt);

  return (
    <Card className="h-full">
      <CardContent className="space-y-4 p-4">
        {mode === "existing" && sessionQuery.data && sessionQuery.data.run_ids.length > 0 ? (
          <div className="flex flex-wrap gap-2 text-xs">
            {sessionQuery.data.run_ids.map((runId) => (
              <Link
                key={runId}
                to={`/sessions/${selectedSessionId}/replay/${runId}`}
                className="rounded-md border px-2 py-1 text-muted-foreground hover:bg-secondary hover:text-foreground"
              >
                replay {runId.slice(-8)}
              </Link>
            ))}
          </div>
        ) : null}
        {messages.length === 0 ? <EmptyState /> : <MessageList messages={messages} />}
        {pendingInterrupt ? (
          <InterruptCard
            interrupt={pendingInterrupt}
            onAction={(payload) => {
              void resumeInterrupt(payload);
            }}
          />
        ) : null}
        <ComposerInput
          streaming={streaming}
          disabled={blocked}
          onSend={(text) => {
            void sendMessage(text);
          }}
          onStop={stopStreaming}
        />
      </CardContent>
    </Card>
  );
}
