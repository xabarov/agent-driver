import { create } from "zustand";
import {
  hasMetadataContent,
  mergeAssistantMetadata,
  normalizeMetadataFromApi,
  pickMetadata,
  type AssistantMessageMetadata,
  type LlmCompletedPatch,
} from "../lib/messageMetadata";
import {
  parseDeepResearchArtifactPayload,
  type ParsedCompactionNotice,
  type DeepResearchArtifact,
  type DeepResearchState,
  type ParsedSubagentLifecycleEvent,
  type ParsedToolState,
  type SourceLedger,
} from "../lib/events";
import type { PlanningSnapshot } from "../lib/planning";
import {
  mergeSourceEvidence,
  normalizeSourceEvidenceList,
  type SourceEvidence,
} from "../lib/sourceEvidence";
import { stripTextFormToolCalls } from "../lib/stripToolCalls";
import type { DeepResearchViewState, SessionDetailView } from "../types/api";

const PLANNING_TOOL_NAMES = new Set(["todo_write", "planning_state_update"]);
const CONTROL_TOOL_NAMES = new Set([...PLANNING_TOOL_NAMES, "ask_user_question"]);
const assistantRawContent = new Map<string, string>();

export type { AssistantMessageMetadata };

export type ToolCallStatus = "running" | "done" | "failed" | "denied";

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
  sources?: SourceEvidence[];
  subagent?: SubagentLifecycle;
}

export interface SubagentLifecycle {
  groupId?: string;
  groupStatus?: "preparing" | "running" | "joined" | "waiting" | "failed" | "cancelled";
  joinState?: string;
  childRuns?: SubagentChildRun[];
}

export interface SubagentChildRun {
  taskId: string;
  subagentRunId?: string;
  childRunId?: string;
  status: "spawned" | "running" | "completed" | "failed" | "cancelled";
  description?: string;
  outputPreview?: string;
  usedTools?: string[];
  warning?: string;
}

export interface CompactionNotice {
  id: string;
  role: "compaction";
  compactionId: string;
  status: "running" | "done" | "failed";
  mode?: string;
  reason?: string;
  failureKind?: string;
  summarizedMessageCount?: number;
  attempts?: number;
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
      sources?: SourceEvidence[];
      planningSnapshot?: PlanningSnapshot;
      deepResearch?: DeepResearchState;
    }
  | ToolChatMessage
  | CompactionNotice;

export interface PendingInterrupt {
  runId: string;
  interruptId: string;
  reason: string;
  assistantId?: string;
  title?: string;
  description?: string;
  proposedAction?: Record<string, unknown>;
  allowedActions: string[];
}

export interface SteeringControl {
  queueId: string;
  message: string;
  status: "queued" | "dequeued" | "applied" | "cancelled";
}

function createId(prefix: string): string {
  return `${prefix}_${Math.random().toString(16).slice(2, 10)}`;
}

function isChatRole(role: string): role is "user" | "assistant" {
  return role === "user" || role === "assistant";
}

function steeringControlsFromSession(detail: SessionDetailView): SteeringControl[] {
  const runId = detail.run_ids.at(-1);
  if (!runId) {
    return [];
  }
  const metadata = detail.metadata_by_run?.[runId];
  const rawControls = metadata?.steering_controls ?? metadata?.steeringControls;
  if (!Array.isArray(rawControls)) {
    return [];
  }
  return rawControls
    .filter(
      (item): item is NonNullable<typeof rawControls>[number] =>
        typeof item === "object" && item !== null,
    )
    .map((item) => {
      const queueId = item.queue_id ?? item.queueId;
      const payload = item.payload;
      const message =
        payload && typeof payload === "object" && typeof payload.message === "string"
          ? payload.message
          : (item.kind ?? "steering command");
      const status = item.status;
      const normalizedStatus: SteeringControl["status"] =
        status === "dequeued" || status === "applied" || status === "cancelled"
          ? status
          : "queued";
      return {
        queueId: typeof queueId === "string" ? queueId : "",
        message,
        status: normalizedStatus,
      };
    })
    .filter((item) => item.queueId);
}

function insertAfterAssistant(
  messages: ChatMessage[],
  assistantId: string,
  item: ChatMessage,
): ChatMessage[] {
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

function sourceEvidenceFromRawMetadata(raw: unknown): SourceEvidence[] {
  if (!raw || typeof raw !== "object") {
    return [];
  }
  const record = raw as Record<string, unknown>;
  return normalizeSourceEvidenceList(record.source_evidence ?? record.sourceEvidence);
}

function deepResearchFromRawMetadata(raw: unknown): DeepResearchState | undefined {
  if (!raw || typeof raw !== "object") {
    return undefined;
  }
  const record = raw as Record<string, unknown>;
  const artifact = parseDeepResearchArtifactPayload(
    record.deep_research_artifacts ?? record.deepResearchArtifacts,
  );
  return artifact ? { artifact, progress: [] } : undefined;
}

function mergeDeepResearchViewFromStream(
  view: DeepResearchViewState | undefined,
  patch: {
    ledger?: SourceLedger;
    progress?: DeepResearchState["progress"][number];
    artifact?: DeepResearchArtifact;
  },
): DeepResearchViewState | undefined {
  if (!view || view.researchMode !== "deep") {
    return view;
  }
  const sourceDomains = new Set<string>();
  const nextSources = patch.ledger
    ? {
        verified: patch.ledger.verifiedReads.length,
        candidates: patch.ledger.searchCandidates.length,
        blocked: patch.ledger.blockedReads.length,
        failed: patch.ledger.failedReads.length,
        distinctDomains: [
          ...patch.ledger.verifiedReads,
          ...patch.ledger.searchCandidates,
          ...patch.ledger.blockedReads,
          ...patch.ledger.failedReads,
        ].reduce((count, source) => {
          if (source.domain && !sourceDomains.has(source.domain)) {
            sourceDomains.add(source.domain);
            return count + 1;
          }
          return count;
        }, 0),
      }
    : view.sources;
  const nextReport = patch.artifact
    ? {
        path: patch.artifact.reportPath,
        kind: "research_report",
        sizeBytes: patch.artifact.reportSizeBytes ?? view.artifacts.report?.sizeBytes ?? 0,
        modifiedAt: view.artifacts.report?.modifiedAt ?? null,
        lifecycle: patch.artifact.capturedLongAnswers ? "captured_inline" : "created",
        previewAvailable: true,
      }
    : view.artifacts.report;
  const nextSourceLedger = patch.ledger
    ? {
        path: "research/sources.jsonl",
        kind: "research_sources",
        sizeBytes: view.artifacts.sourceLedger?.sizeBytes ?? 0,
        modifiedAt: view.artifacts.sourceLedger?.modifiedAt ?? null,
        lifecycle: "created",
        previewAvailable: true,
      }
    : view.artifacts.sourceLedger;
  const nextPhase = patch.progress?.label || view.phase;
  const nextWarnings = view.warnings.filter((warning) => {
    if (nextReport && warning === "deep_research_no_report_artifact") {
      return false;
    }
    if (nextSourceLedger && warning === "deep_research_no_source_ledger_artifact") {
      return false;
    }
    return true;
  });
  const nextReadiness =
    nextReport && nextSourceLedger && nextWarnings.length === 0
      ? "ready"
      : !nextReport
        ? "needs_report"
        : !nextSourceLedger
          ? "needs_more_sources"
          : view.readiness;
  return {
    ...view,
    phase: nextPhase,
    sources: nextSources,
    artifacts: {
      ...view.artifacts,
      report: nextReport,
      sourceLedger: nextSourceLedger,
    },
    readiness: nextReadiness,
    warnings: nextWarnings,
  };
}

function compactionNoticeFromRawMetadata(
  raw: unknown,
  fallbackId: string,
): ParsedCompactionNotice | undefined {
  if (!raw || typeof raw !== "object") {
    return undefined;
  }
  const record = raw as Record<string, unknown>;
  const rawCompaction = record.compaction;
  if (!rawCompaction || typeof rawCompaction !== "object") {
    return undefined;
  }
  const compaction = rawCompaction as Record<string, unknown>;
  const rawStatus = compaction.status;
  const status =
    rawStatus === "failed" || rawStatus === "running" || rawStatus === "done"
      ? rawStatus
      : "done";
  const summarized = compaction.summarized_message_count ?? compaction.summarizedMessageCount;
  const attempts = compaction.attempts;
  return {
    compactionId:
      typeof compaction.compaction_id === "string"
        ? compaction.compaction_id
        : typeof compaction.compactionId === "string"
          ? compaction.compactionId
          : fallbackId,
    status,
    mode: typeof compaction.mode === "string" ? compaction.mode : undefined,
    reason: typeof compaction.reason === "string" ? compaction.reason : undefined,
    failureKind:
      typeof compaction.failure_kind === "string"
        ? compaction.failure_kind
        : typeof compaction.failureKind === "string"
          ? compaction.failureKind
          : undefined,
    summarizedMessageCount:
      typeof summarized === "number" && Number.isFinite(summarized)
        ? summarized
        : undefined,
    attempts:
      typeof attempts === "number" && Number.isFinite(attempts) ? attempts : undefined,
  };
}

function attachSourcesToNearestAssistant(
  messages: ChatMessage[],
  toolCallId: string,
  sources: SourceEvidence[],
): ChatMessage[] {
  if (!sources.length) {
    return messages;
  }
  const toolIndex = messages.findIndex(
    (message) => message.role === "tool" && message.toolCallId === toolCallId,
  );
  if (toolIndex < 0) {
    return messages;
  }
  for (let index = toolIndex - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message?.role !== "assistant") {
      continue;
    }
    return messages.map((item, itemIndex) =>
      itemIndex === index && item.role === "assistant"
        ? {
            ...item,
            sources: mergeSourceEvidence([...(item.sources ?? []), ...sources]),
          }
        : item,
    );
  }
  return messages;
}

function sourcesForAssistantTurn(
  messages: ChatMessage[],
  assistantId: string,
): SourceEvidence[] {
  const assistantIndex = messages.findIndex(
    (message) => message.id === assistantId && message.role === "assistant",
  );
  if (assistantIndex < 0) {
    return [];
  }
  let start = assistantIndex - 1;
  while (start >= 0 && messages[start]?.role !== "user") {
    start -= 1;
  }
  const sources: SourceEvidence[] = [];
  for (const message of messages.slice(start + 1)) {
    if (message.role === "user") {
      break;
    }
    if (message.role === "tool" && message.sources?.length) {
      sources.push(...message.sources);
    }
  }
  return mergeSourceEvidence(sources);
}

function updateLatestAgentTool(
  messages: ChatMessage[],
  assistantId: string,
  update: (message: ToolChatMessage) => ToolChatMessage,
): ChatMessage[] {
  const assistantIndex = messages.findIndex(
    (message) => message.id === assistantId && message.role === "assistant",
  );
  let fallbackIndex = -1;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message?.role !== "tool" || message.name !== "agent_tool") {
      continue;
    }
    if (fallbackIndex < 0) {
      fallbackIndex = index;
    }
    if (assistantIndex >= 0 && index > assistantIndex) {
      return messages.map((item, itemIndex) =>
        itemIndex === index && item.role === "tool" ? update(item) : item,
      );
    }
  }
  if (fallbackIndex >= 0) {
    return messages.map((item, itemIndex) =>
      itemIndex === fallbackIndex && item.role === "tool" ? update(item) : item,
    );
  }
  return messages;
}

function upsertChildRun(
  children: SubagentChildRun[],
  patch: SubagentChildRun,
): SubagentChildRun[] {
  const index = children.findIndex(
    (child) =>
      child.taskId === patch.taskId ||
      Boolean(patch.subagentRunId && child.subagentRunId === patch.subagentRunId) ||
      Boolean(patch.childRunId && child.childRunId === patch.childRunId),
  );
  if (index < 0) {
    return [...children, patch];
  }
  return children.map((child, childIndex) =>
    childIndex === index ? { ...child, ...patch } : child,
  );
}

function applySubagentPatch(
  lifecycle: SubagentLifecycle | undefined,
  event: ParsedSubagentLifecycleEvent,
): SubagentLifecycle {
  const next: SubagentLifecycle = {
    ...(lifecycle ?? {}),
    childRuns: [...(lifecycle?.childRuns ?? [])],
  };
  if (event.groupId) {
    next.groupId = event.groupId;
  }
  if (event.event === "subagent_group_started") {
    next.groupStatus = "running";
  } else if (event.event === "subagent_group_joined") {
    next.groupStatus = "joined";
    next.joinState = event.joinState;
  } else if (event.event === "subagent_group_join_waiting") {
    next.groupStatus = "waiting";
    next.joinState = event.joinState;
  } else if (event.event === "subagent_group_failed") {
    next.groupStatus = "failed";
    next.joinState = event.joinState;
  }
  if (event.childRun) {
    next.childRuns = upsertChildRun(next.childRuns ?? [], event.childRun);
  }
  return next;
}

function upsertCompactionNotice(
  messages: ChatMessage[],
  assistantId: string,
  notice: ParsedCompactionNotice,
): ChatMessage[] {
  const existingIndex = messages.findIndex(
    (message) =>
      message.role === "compaction" && message.compactionId === notice.compactionId,
  );
  if (existingIndex >= 0) {
    return messages.map((message, index) =>
      index === existingIndex && message.role === "compaction"
        ? { ...message, ...notice }
        : message,
    );
  }
  const item: CompactionNotice = {
    id: createId("compaction"),
    role: "compaction",
    ...notice,
  };
  return insertAfterAssistant(messages, assistantId, item);
}

interface ChatState {
  messages: ChatMessage[];
  streaming: boolean;
  lastSeq: number;
  sessionId?: string;
  runId?: string;
  pendingInterrupt?: PendingInterrupt;
  steeringControls: SteeringControl[];
  deepResearchView?: DeepResearchViewState;
  lastError?: string;
  beginUserTurn: (text: string) => string;
  appendDelta: (assistantId: string, text: string) => void;
  replaceAssistantContent: (assistantId: string, text: string) => void;
  tombstoneAssistant: (assistantId: string) => void;
  appendToolStarted: (assistantId: string, tool: ParsedToolState) => void;
  updateToolCompleted: (toolCallId: string, tool: ParsedToolState) => void;
  applySubagentLifecycle: (
    assistantId: string,
    event: ParsedSubagentLifecycleEvent,
  ) => void;
  upsertCompactionNotice: (
    assistantId: string,
    notice: ParsedCompactionNotice,
  ) => void;
  updateDeepResearch: (
    assistantId: string,
    patch: {
      ledger?: SourceLedger;
      progress?: DeepResearchState["progress"][number];
      artifact?: DeepResearchArtifact;
    },
  ) => void;
  finishTurn: (assistantId: string) => void;
  setStreaming: (value: boolean) => void;
  setLastSeq: (seq: number) => void;
  setSessionId: (sessionId?: string) => void;
  setRunId: (runId?: string) => void;
  setPendingInterrupt: (interrupt?: PendingInterrupt) => void;
  setDeepResearchView: (state?: DeepResearchViewState) => void;
  addSteeringControl: (control: SteeringControl) => void;
  updateSteeringControl: (queueId: string, status: SteeringControl["status"]) => void;
  appendAssistantMetadata: (assistantId: string, patch: LlmCompletedPatch) => void;
  setPlanningSnapshot: (assistantId: string, snapshot: PlanningSnapshot) => void;
  setAssistantRunId: (assistantId: string, runId: string) => void;
  setLastError: (message?: string) => void;
  setMessages: (messages: ChatMessage[]) => void;
  deleteMessage: (messageId: string) => void;
  prepareRetry: (
    assistantId: string,
  ) => { userText: string; newAssistantId: string; retryFromRunId?: string } | null;
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
  steeringControls: [],
  deepResearchView: undefined,
  lastError: undefined,
  beginUserTurn: (text) => {
    const userId = createId("user");
    const assistantId = createId("assistant");
    assistantRawContent.set(assistantId, "");
    set((state) => ({
      streaming: true,
      lastSeq: 0,
      runId: undefined,
      pendingInterrupt: undefined,
      steeringControls: [],
      deepResearchView: undefined,
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
    set((state) => {
      const previousRaw = assistantRawContent.get(assistantId);
      const index = state.messages.findIndex(
        (message) => message.id === assistantId && message.role === "assistant",
      );
      if (index < 0) {
        assistantRawContent.set(assistantId, text);
        return {
          messages: [
            ...state.messages,
            {
              id: assistantId,
              role: "assistant",
              content: stripTextFormToolCalls(text),
              pending: true,
            },
          ],
        };
      }
      return {
        messages: state.messages.map((message) => {
          if (message.id !== assistantId || message.role !== "assistant") {
            return message;
          }
          const merged = `${previousRaw ?? message.content}${text}`;
          assistantRawContent.set(assistantId, merged);
          return { ...message, content: stripTextFormToolCalls(merged) };
        }),
      };
    });
  },
  replaceAssistantContent: (assistantId, text) => {
    assistantRawContent.set(assistantId, text);
    set((state) => {
      const hasAssistant = state.messages.some(
        (message) => message.id === assistantId && message.role === "assistant",
      );
      if (!hasAssistant) {
        return {
          messages: [
            ...state.messages,
            {
              id: assistantId,
              role: "assistant",
              content: stripTextFormToolCalls(text),
              pending: false,
            },
          ],
        };
      }
      return {
        messages: state.messages.map((message) =>
          message.id === assistantId && message.role === "assistant"
            ? { ...message, content: stripTextFormToolCalls(text) }
            : message,
        ),
      };
    });
  },
  tombstoneAssistant: (assistantId) =>
    set((state) => {
      const index = state.messages.findIndex(
        (message) => message.id === assistantId && message.role === "assistant",
      );
      if (index < 0) {
        return state;
      }
      let end = index + 1;
      while (end < state.messages.length && state.messages[end]?.role === "tool") {
        end += 1;
      }
      const terminalTools = state.messages
        .slice(index + 1, end)
        .filter(
          (message): message is ToolChatMessage =>
            message.role === "tool" && message.status !== "running",
        );
      assistantRawContent.delete(assistantId);
      const target = state.messages[index];
      if (target?.role === "assistant" && target.planningSnapshot) {
        return {
          messages: [
            ...state.messages.slice(0, index),
            { ...target, content: "", pending: false },
            ...terminalTools,
            ...state.messages.slice(end),
          ],
        };
      }
      return {
        messages: [
          ...state.messages.slice(0, index),
          ...terminalTools,
          ...state.messages.slice(end),
        ],
      };
    }),
  appendToolStarted: (assistantId, tool) =>
    set((state) => {
      if (CONTROL_TOOL_NAMES.has(tool.name)) {
        return state;
      }
      if (
        state.messages.some(
          (item) => item.role === "tool" && item.toolCallId === tool.toolCallId,
        )
      ) {
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
        sources: tool.sources,
      };
      return {
        messages: insertAfterAssistant(state.messages, assistantId, toolMessage),
      };
    }),
  updateToolCompleted: (toolCallId, tool) =>
    set((state) => {
      const sources = tool.sources ?? [];
      const messages = state.messages.map((message) =>
        message.role === "tool" && message.toolCallId === toolCallId
          ? {
              ...message,
              status: tool.status,
              resultPreview: tool.resultPreview ?? message.resultPreview,
              durationMs: tool.durationMs ?? message.durationMs,
              sources: mergeSourceEvidence([...(message.sources ?? []), ...sources]),
            }
          : message,
      );
      return {
        messages: attachSourcesToNearestAssistant(messages, toolCallId, sources),
      };
    }),
  applySubagentLifecycle: (assistantId, event) =>
    set((state) => ({
      messages: updateLatestAgentTool(state.messages, assistantId, (message) => ({
        ...message,
        subagent: applySubagentPatch(message.subagent, event),
      })),
    })),
  upsertCompactionNotice: (assistantId, notice) =>
    set((state) => ({
      messages: upsertCompactionNotice(state.messages, assistantId, notice),
    })),
  updateDeepResearch: (assistantId, patch) =>
    set((state) => ({
      deepResearchView: mergeDeepResearchViewFromStream(state.deepResearchView, patch),
      messages: state.messages.map((message) => {
        if (message.id !== assistantId || message.role !== "assistant") {
          return message;
        }
        const current = message.deepResearch ?? { progress: [] };
        return {
          ...message,
          deepResearch: {
            ledger: patch.ledger ?? current.ledger,
            artifact: patch.artifact ?? current.artifact,
            progress: patch.progress
              ? [...current.progress, patch.progress].slice(-6)
              : current.progress,
          },
        };
      }),
    })),
  finishTurn: (assistantId) => {
    set((state) => ({
      streaming: false,
      messages: state.messages.map((message) => {
        if (message.id !== assistantId || message.role !== "assistant") {
          return message;
        }
        const rawContent = assistantRawContent.get(assistantId) ?? message.content;
        const content = rawContent ? stripTextFormToolCalls(rawContent) : rawContent;
        const sources = sourcesForAssistantTurn(state.messages, assistantId);
        assistantRawContent.delete(assistantId);
        return {
          ...message,
          pending: false,
          content,
          sources: mergeSourceEvidence([...(message.sources ?? []), ...sources]),
        };
      }),
    }));
  },
  setStreaming: (value) => set({ streaming: value }),
  setLastSeq: (seq) => set({ lastSeq: seq }),
  setSessionId: (sessionId) => set({ sessionId }),
  setRunId: (runId) => set({ runId }),
  setPendingInterrupt: (pendingInterrupt) => set({ pendingInterrupt }),
  setDeepResearchView: (deepResearchView) => set({ deepResearchView }),
  addSteeringControl: (control) =>
    set((state) => {
      if (state.steeringControls.some((item) => item.queueId === control.queueId)) {
        return state;
      }
      return { steeringControls: [...state.steeringControls, control] };
    }),
  updateSteeringControl: (queueId, status) =>
    set((state) => ({
      steeringControls: state.steeringControls.map((item) =>
        item.queueId === queueId ? { ...item, status } : item,
      ),
    })),
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
    set((state) => {
      const hasTarget = state.messages.some(
        (message) => message.id === assistantId && message.role === "assistant",
      );
      if (!hasTarget) {
        return state;
      }
      return {
        messages: state.messages.map((message) =>
          message.id === assistantId && message.role === "assistant"
            ? { ...message, planningSnapshot: snapshot }
            : message,
        ),
      };
    }),
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
        for (const message of state.messages.slice(index, end)) {
          if (message.role === "assistant") {
            assistantRawContent.delete(message.id);
          }
        }
        return {
          messages: [...state.messages.slice(0, index), ...state.messages.slice(end)],
        };
      }
      if (target.role === "assistant") {
        let end = index + 1;
        while (end < state.messages.length && state.messages[end]?.role === "tool") {
          end += 1;
        }
        assistantRawContent.delete(target.id);
        return {
          messages: [...state.messages.slice(0, index), ...state.messages.slice(end)],
        };
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
    const retryFromRunId = assistant.runId;
    assistantRawContent.delete(assistantId);
    assistantRawContent.set(newAssistantId, "");
    set({
      streaming: true,
      lastSeq: 0,
      runId: undefined,
      pendingInterrupt: undefined,
      steeringControls: [],
      deepResearchView: undefined,
      lastError: undefined,
      messages: [
        ...state.messages.slice(0, index),
        { id: newAssistantId, role: "assistant", content: "", pending: true },
      ],
    });
    return {
      userText: userMessage.content,
      newAssistantId,
      ...(retryFromRunId ? { retryFromRunId } : {}),
    };
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
    const messages: ChatMessage[] = [];
    for (const item of detail.transcript.filter(
      (entry): entry is SessionDetailView["transcript"][number] =>
        isChatRole(entry.role),
    )) {
      if (item.role === "assistant") {
        const runId = detail.run_ids[assistantRunIndex];
        assistantRunIndex += 1;
        const runMetadata =
          runId && detail.metadata_by_run ? detail.metadata_by_run[runId] : undefined;
        const fromRun = runMetadata ? normalizeMetadataFromApi(runMetadata) : undefined;
        const runSources = runMetadata ? sourceEvidenceFromRawMetadata(runMetadata) : [];
        const fromTranscript = normalizeMetadataFromApi(item.metadata ?? undefined);
        const transcriptSources = sourceEvidenceFromRawMetadata(item.metadata);
        const deepResearch =
          deepResearchFromRawMetadata(runMetadata) ??
          deepResearchFromRawMetadata(item.metadata);
        const serverMetadata = fromTranscript ?? fromRun;
        const localMetadata =
          (runId ? priorByRunId.get(runId) : undefined) ??
          priorAssistants[assistantRunIndex - 1]?.metadata;
        const sources = mergeSourceEvidence([...transcriptSources, ...runSources]);
        const assistantId = createId(item.role);
        messages.push({
          id: assistantId,
          role: "assistant",
          content: stripTextFormToolCalls(item.content),
          pending: false,
          runId,
          metadata: pickMetadata(serverMetadata, localMetadata),
          sources,
          deepResearch,
        });
        const compactionNotice = compactionNoticeFromRawMetadata(
          runMetadata ?? item.metadata,
          `${runId ?? assistantId}:compaction`,
        );
        if (compactionNotice) {
          messages.push({
            id: createId("compaction"),
            role: "compaction",
            ...compactionNotice,
          });
        }
        continue;
      }
      messages.push({
        id: createId(item.role),
        role: "user",
        content: item.content,
      });
    }
    assistantRawContent.clear();
    set({
      streaming: false,
      lastSeq: 0,
      sessionId: detail.session_id,
      runId: detail.run_ids.at(-1),
      pendingInterrupt: undefined,
      steeringControls: steeringControlsFromSession(detail),
      deepResearchView: undefined,
      lastError: undefined,
      messages,
    });
  },
  reset: () => {
    assistantRawContent.clear();
    set({
      messages: [],
      streaming: false,
      lastSeq: 0,
      sessionId: undefined,
      runId: undefined,
      pendingInterrupt: undefined,
      steeringControls: [],
      deepResearchView: undefined,
      lastError: undefined,
    });
  },
}));
