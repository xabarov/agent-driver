import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  Bot,
  BookOpenCheck,
  FileText,
  FileWarning,
  ListChecks,
} from "lucide-react";
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
  const latestSessionRunId = sessionQuery.data?.run_ids.at(-1);
  const activeRunId = streaming && runId ? runId : (latestSessionRunId ?? runId);
  const deepResearchStateQuery = useQuery({
    queryKey: ["deep-research-state", activeRunId ?? "no-run"],
    queryFn: () => fetchDeepResearchState(activeRunId ?? ""),
    enabled: Boolean(activeRunId),
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

  useEffect(() => {
    if (!activeRunId || (deepResearchView && deepResearchView.runId !== activeRunId)) {
      setDeepResearchView(undefined);
    }
  }, [activeRunId, deepResearchView?.runId, setDeepResearchView]);

  useEffect(() => {
    if (deepResearchStateQuery.isError && !streaming) {
      setDeepResearchView(undefined);
    }
  }, [deepResearchStateQuery.isError, setDeepResearchView, streaming]);

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
          <DeepResearchCockpit state={deepResearchView} />
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
        {state.researchMode}: {state.profile === "hard" ? "Hard" : "Medium"}
      </Badge>
      <Badge variant="outline">{state.profileSource}</Badge>
      <span className="inline-flex items-center gap-1.5">
        <ListChecks className="h-3.5 w-3.5" />
        {state.phase}
        {state.todos.total > 0 ? ` · ${state.todos.done}/${state.todos.total}` : ""}
      </span>
      <span>
        sources {state.sources.verified}/{state.sources.requiredVerified || 1} verified ·{" "}
        {state.sources.candidates} candidate
        {state.sources.blocked ? ` · ${state.sources.blocked} blocked` : ""}
      </span>
      <Badge variant={state.sources.qualityOk ? "outline" : "secondary"}>
        quality: {state.sources.qualityStatus}
      </Badge>
      <span className="inline-flex items-center gap-1.5">
        <FileText className="h-3.5 w-3.5" />
        {report
          ? `${report.lifecycle} · ${report.path} · ${formatBytes(report.sizeBytes)}`
          : "report not started"}
      </span>
      {state.metrics.totalTokens ? (
        <span className="tabular-nums">{state.metrics.totalTokens.toLocaleString()} tokens</span>
      ) : null}
      {hasWarnings ? (
        <span className="inline-flex items-center gap-1.5 text-amber-700 dark:text-amber-300">
          <AlertTriangle className="h-3.5 w-3.5" />
          {state.readiness}
        </span>
      ) : null}
    </div>
  );
}

function DeepResearchCockpit({
  state,
}: {
  state?: DeepResearchViewState;
}) {
  if (!state || state.researchMode !== "deep") {
    return null;
  }
  const artifacts = [
    state.artifacts.report,
    state.artifacts.sourceLedger,
    state.artifacts.claims,
  ].filter((artifact): artifact is NonNullable<typeof artifact> => Boolean(artifact));
  const sourceRows = state.sources.rows.slice(0, 6);
  return (
    <section
      aria-label="Deep Research cockpit"
      className="rounded-md border border-border/70 bg-background/70 p-3 text-xs text-muted-foreground"
    >
      <div className="mb-3 flex flex-wrap items-center gap-2 border-b border-border/60 pb-2">
        {["Overview", "Sources", "Artifacts", "Subagents", "Trace"].map((label) => (
          <Badge key={label} variant="outline" className="rounded-full">
            {label}
          </Badge>
        ))}
      </div>
      <div className="grid gap-3 md:grid-cols-5">
      <div className="space-y-1">
        <div className="flex items-center gap-1.5 font-medium text-foreground">
          <ListChecks className="h-3.5 w-3.5" />
          Overview
        </div>
        <div>phase: {state.phase}</div>
        <div>readiness: {state.readiness}</div>
        <div>profile: {state.profile} ({state.profileSource})</div>
        <div>
          todos: {state.todos.done}/{state.todos.total}
          {state.todos.stale ? " · stale" : ""}
        </div>
        {state.todos.current ? <div className="truncate">current: {state.todos.current}</div> : null}
      </div>
      <div className="space-y-1">
        <div className="flex items-center gap-1.5 font-medium text-foreground">
          <BookOpenCheck className="h-3.5 w-3.5" />
          Sources
        </div>
        <div>
          verified {state.sources.verified}/{state.sources.requiredVerified || 1} · candidates{" "}
          {state.sources.candidates}
        </div>
        <div>
          blocked {state.sources.blocked} · failed {state.sources.failed} · domains{" "}
          {state.sources.distinctDomains}
        </div>
        <div>quality: {state.sources.qualityStatus}</div>
        {sourceRows.length ? (
          <ul className="mt-1 space-y-1">
            {sourceRows.map((source, index) => (
              <li key={`${source.status}:${source.url ?? index}`} className="truncate">
                {source.status}: {source.domain ?? source.title ?? source.url ?? "source"}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      <div className="space-y-1">
        <div className="flex items-center gap-1.5 font-medium text-foreground">
          <FileText className="h-3.5 w-3.5" />
          Artifacts
        </div>
        {artifacts.length ? (
          artifacts.map((artifact) => (
            <div key={artifact.path} className="truncate">
              {artifact.path} · {artifact.lifecycle} ·{" "}
              {artifact.previewAvailable ? "preview/download" : "metadata only"}
            </div>
          ))
        ) : (
          <div>waiting for first artifact</div>
        )}
      </div>
      <div className="space-y-1">
        <div className="flex items-center gap-1.5 font-medium text-foreground">
          <Bot className="h-3.5 w-3.5" />
          Subagents
        </div>
        <div>
          children {state.subagents.completedChildren}/{state.subagents.totalChildren}
          {state.subagents.failedChildren ? ` · failed ${state.subagents.failedChildren}` : ""}
        </div>
        <div>sources from children: {state.subagents.sourceRecords}</div>
        <div>summary chars: {state.subagents.summaryChars}</div>
        {state.subagents.duplicatedQueries ? (
          <div>duplicated queries: {state.subagents.duplicatedQueries}</div>
        ) : null}
        {state.subagents.toolNames.length ? (
          <div className="truncate">tools: {state.subagents.toolNames.join(", ")}</div>
        ) : null}
      </div>
      <div className="space-y-1">
        <div className="flex items-center gap-1.5 font-medium text-foreground">
          <FileWarning className="h-3.5 w-3.5" />
          Trace
        </div>
        <div>run: {state.runId}</div>
        <div>trace: {state.trace.verdict ?? "pending"}</div>
        <div>terminal: {state.trace.terminalEvent ?? "streaming"}</div>
        <div>Phoenix/export: pending</div>
        {state.warnings.length ? (
          <div className="flex items-start gap-1.5 text-amber-700 dark:text-amber-300">
            <FileWarning className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>{state.warnings.join(", ")}</span>
          </div>
        ) : null}
      </div>
      </div>
    </section>
  );
}

function formatBytes(value: number): string {
  if (value < 1024) {
    return `${value} B`;
  }
  return `${(value / 1024).toFixed(1)} KB`;
}
