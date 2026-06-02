import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, BookOpenCheck, FileText, ListChecks } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";

import { fetchDeepResearchState } from "../../lib/api";
import { cn } from "../../lib/cn";
import { useSession } from "../../lib/sessions";
import { useRunStream } from "../../hooks/useRunStream";
import { useChatStore } from "../../store/chatStore";
import type { DeepResearchViewState, WorkspaceArtifactView } from "../../types/api";
import { Badge } from "../ui/badge";
import { ChatComposer } from "./ChatComposer";
import { EmptyState } from "./EmptyState";
import { FakeProviderBanner } from "./FakeProviderBanner";
import { InterruptCard } from "./InterruptCard";
import { MessageList } from "./MessageList";
import { SessionRunsMenu } from "./SessionRunsMenu";
import { StreamErrorBanner } from "./StreamErrorBanner";
import { WorkspaceArtifactsPanel } from "./WorkspaceArtifactsPanel";

interface ChatPageProps {
  mode: "new" | "existing";
}

export function ChatPage({ mode }: ChatPageProps) {
  const navigate = useNavigate();
  const params = useParams<{ id: string }>();
  const sessionId = useChatStore((state) => state.sessionId);
  const runId = useChatStore((state) => state.runId);
  const pendingInterrupt = useChatStore((state) => state.pendingInterrupt);
  const reset = useChatStore((state) => state.reset);
  const loadSession = useChatStore((state) => state.loadSession);
  const setDeepResearchView = useChatStore((state) => state.setDeepResearchView);
  const messages = useChatStore((state) => state.messages);
  const streaming = useChatStore((state) => state.streaming);
  const steeringControls = useChatStore((state) => state.steeringControls);
  const deepResearchView = useChatStore((state) => state.deepResearchView);
  const {
    sendMessage,
    steerRun,
    cancelSteering,
    retryAssistant,
    resumeInterrupt,
    stopStreaming,
  } = useRunStream();
  const selectedSessionId = mode === "existing" ? params.id ?? "" : "";
  const artifactSessionId = selectedSessionId || sessionId || "";
  const sessionQuery = useSession(selectedSessionId);
  const latestRunId = sessionQuery.data?.run_ids.at(-1) ?? runId;
  const deepResearchStateQuery = useQuery({
    queryKey: ["deep-research-state", latestRunId ?? "no-run"],
    queryFn: () => fetchDeepResearchState(latestRunId ?? ""),
    enabled: Boolean(latestRunId),
    staleTime: streaming ? 2_000 : 15_000,
    retry: false,
  });

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

  useEffect(() => {
    if (deepResearchStateQuery.data) {
      setDeepResearchView(deepResearchStateQuery.data);
    }
  }, [deepResearchStateQuery.data, setDeepResearchView]);

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
        {artifactSessionId ? (
          <div className="mb-2 flex justify-end gap-1">
            <WorkspaceArtifactsPanel
              sessionId={artifactSessionId}
              disabled={streaming && !sessionId && !selectedSessionId}
              knownArtifacts={deepResearchArtifacts(deepResearchView)}
            />
            {mode === "existing" &&
            sessionQuery.data &&
            sessionQuery.data.run_ids.length > 0 ? (
              <SessionRunsMenu
                sessionId={selectedSessionId}
                runIds={sessionQuery.data.run_ids}
              />
            ) : null}
          </div>
        ) : null}
        <div className="mb-2 space-y-2">
          <FakeProviderBanner />
          <StreamErrorBanner />
          <DeepResearchCompactBar state={deepResearchView} />
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

function deepResearchArtifacts(
  state: DeepResearchViewState | undefined,
): WorkspaceArtifactView[] {
  if (!state) {
    return [];
  }
  return [
    state.artifacts.report,
    state.artifacts.sourceLedger,
    state.artifacts.claims,
  ]
    .filter((artifact): artifact is NonNullable<typeof artifact> => Boolean(artifact))
    .map((artifact) => ({
      path: artifact.path,
      kind: artifact.kind,
      sizeBytes: artifact.sizeBytes,
      modifiedAt: artifact.modifiedAt ?? "",
    }));
}

function DeepResearchCompactBar({
  state,
}: {
  state?: DeepResearchViewState;
}) {
  if (!state || state.researchMode !== "deep") {
    return null;
  }
  const report = state.artifacts.report;
  const hasWarnings = state.warnings.length > 0 || state.readiness !== "ready";
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 text-xs",
        "border-border/80 bg-card/80 text-muted-foreground shadow-sm",
      )}
      aria-live="polite"
    >
      <Badge
        variant="outline"
        className="gap-1.5 border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
      >
        <BookOpenCheck className="h-3.5 w-3.5" />
        Deep: {state.profile === "hard" ? "Hard" : "Medium"}
      </Badge>
      <span className="inline-flex items-center gap-1.5">
        <ListChecks className="h-3.5 w-3.5" />
        {state.phase}
        {state.todos.total > 0 ? ` · ${state.todos.done}/${state.todos.total}` : ""}
      </span>
      <span>
        sources {state.sources.verified} verified · {state.sources.candidates} candidate
        {state.sources.blocked ? ` · ${state.sources.blocked} blocked` : ""}
      </span>
      <span className="inline-flex items-center gap-1.5">
        <FileText className="h-3.5 w-3.5" />
        {report ? `${report.path} · ${formatBytes(report.sizeBytes)}` : "report not started"}
      </span>
      {hasWarnings ? (
        <span className="inline-flex items-center gap-1.5 text-amber-700 dark:text-amber-300">
          <AlertTriangle className="h-3.5 w-3.5" />
          {state.readiness}
        </span>
      ) : null}
    </div>
  );
}

function formatBytes(value: number): string {
  if (value < 1024) {
    return `${value} B`;
  }
  return `${(value / 1024).toFixed(1)} KB`;
}
