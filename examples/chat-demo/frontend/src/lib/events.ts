import {
  mergeAssistantMetadata,
  parseLlmCompletedData,
  type AssistantMessageMetadata,
} from "./messageMetadata";
import { parsePlanningSnapshot } from "./planning";
import { stripTextFormToolCalls } from "./stripToolCalls";
import type { ChatMessage, ToolCallStatus } from "../store/chatStore";
import type { PlanningSnapshot } from "./planning";

const PLANNING_TOOL_NAMES = new Set(["todo_write", "planning_state_update"]);

export type StreamEventName =
  | "run_started"
  | "run_resumed"
  | "llm_call_started"
  | "llm_call_completed"
  | "assistant_message_started"
  | "assistant_message_completed"
  | "assistant_message_replaced"
  | "assistant_message_tombstoned"
  | "token_delta"
  | "tool_call_started"
  | "tool_call_completed"
  | "interrupt_requested"
  | "run_paused"
  | "checkpoint_saved"
  | "node_started"
  | "node_completed"
  | "run_completed"
  | "run_failed"
  | "run_cancelled"
  | string;

export interface RunStreamEvent<TData = Record<string, unknown>> {
  schema_version: "1.0";
  stream_id: string;
  run_id: string;
  attempt_id: string;
  seq: number;
  event: StreamEventName;
  source: "runtime_event";
  data: TData;
  runtime_event_id?: string | null;
  created_at?: string | null;
}

export interface TokenDeltaData {
  index: number;
  delta_text: string;
}

export interface RunTerminalData {
  finish_reason?: string;
}

export interface ParsedToolState {
  toolCallId: string;
  name: string;
  status: ToolCallStatus;
  argsSummary?: string;
  args?: Record<string, unknown>;
  resultPreview?: string;
  risk?: string;
  durationMs?: number;
}

export function isTokenDelta(event: RunStreamEvent<Record<string, unknown>>): boolean {
  return event.event === "token_delta";
}

export function getTokenDeltaText(event: RunStreamEvent<Record<string, unknown>>): string {
  const delta = event.data.delta_text;
  return typeof delta === "string" ? delta : "";
}

export function getAssistantSnapshotContent(event: RunStreamEvent<Record<string, unknown>>): string | undefined {
  const content = event.data.content;
  return typeof content === "string" ? content : undefined;
}

export function isTerminalEvent(event: RunStreamEvent<Record<string, unknown>>): boolean {
  return (
    event.event === "run_completed" ||
    event.event === "run_failed" ||
    event.event === "run_cancelled"
  );
}

export function isToolCallStarted(event: RunStreamEvent<Record<string, unknown>>): boolean {
  return event.event === "tool_call_started";
}

export function isToolCallCompleted(event: RunStreamEvent<Record<string, unknown>>): boolean {
  return event.event === "tool_call_completed";
}

export function isInterruptEvent(event: RunStreamEvent<Record<string, unknown>>): boolean {
  return event.event === "interrupt_requested" || event.event === "run_paused";
}

function summarizeArgs(args: unknown): string | undefined {
  if (!args || typeof args !== "object") {
    return undefined;
  }
  try {
    const text = JSON.stringify(args);
    return text.length > 120 ? `${text.slice(0, 120)}...` : text;
  } catch {
    return undefined;
  }
}

function toolStateKey(tool: Record<string, unknown>, index: number): string {
  const toolCallId = tool.tool_call_id;
  if (typeof toolCallId === "string" && toolCallId) {
    return toolCallId;
  }
  const name = String(tool.tool_name ?? "?");
  return `${name}:${index}`;
}

export function parseToolStatesFromEvent(
  event: RunStreamEvent<Record<string, unknown>>,
): ParsedToolState[] {
  const tools = event.data.tools;
  if (!Array.isArray(tools)) {
    const singleName = event.data.tool_name;
    if (typeof singleName === "string") {
      return [
        {
          toolCallId: String(event.data.tool_call_id ?? singleName),
          name: singleName,
          status: event.event === "tool_call_completed" ? "done" : "running",
          args: typeof event.data.args === "object" ? (event.data.args as Record<string, unknown>) : undefined,
          argsSummary: summarizeArgs(event.data.args),
          resultPreview:
            typeof event.data.result_summary === "string"
              ? event.data.result_summary
              : undefined,
        },
      ];
    }
    return [];
  }
  return tools
    .filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null)
    .map((tool, index) => {
      const rawStatus = tool.status;
      let status: ToolCallStatus = event.event === "tool_call_completed" ? "done" : "running";
      if (rawStatus === "denied") {
        status = "denied";
      } else if (rawStatus === "failed" || rawStatus === "error") {
        status = "failed";
      }
      return {
        toolCallId: toolStateKey(tool, index),
        name: String(tool.tool_name ?? "?"),
        status,
        args: typeof tool.args === "object" ? (tool.args as Record<string, unknown>) : undefined,
        argsSummary: summarizeArgs(tool.args),
        resultPreview:
          typeof tool.result_summary === "string" ? tool.result_summary : undefined,
        risk: typeof tool.risk === "string" ? tool.risk : undefined,
        durationMs:
          typeof tool.duration_ms === "number" ? tool.duration_ms : undefined,
      };
    });
}

export function buildLastEventId(runId: string | undefined, seq: number): string | undefined {
  if (!runId || seq <= 0) {
    return undefined;
  }
  return `${runId}:${seq}`;
}

function applyPlanningToAssistantMessage(
  messages: ChatMessage[],
  assistantId: string,
  snapshot: PlanningSnapshot,
  content: string,
  metadata?: AssistantMessageMetadata,
): void {
  const index = messages.findIndex((item) => item.id === assistantId && item.role === "assistant");
  if (index >= 0 && messages[index].role === "assistant") {
    messages[index] = { ...messages[index], planningSnapshot: snapshot };
    return;
  }
  messages.push({
    id: assistantId,
    role: "assistant",
    content,
    pending: false,
    metadata,
    planningSnapshot: snapshot,
  });
}

export function eventsToMessages(events: RunStreamEvent<Record<string, unknown>>[]): ChatMessage[] {
  const messages: ChatMessage[] = [];
  let assistantId: string | null = null;
  let assistantContent = "";
  let assistantRawContent = "";
  let assistantMetadata: AssistantMessageMetadata | undefined = undefined;
  let assistantPlanningSnapshot: PlanningSnapshot | undefined = undefined;
  let seq = 0;

  const flushAssistant = () => {
    if (assistantId && (assistantContent.trim() || assistantPlanningSnapshot)) {
      messages.push({
        id: assistantId,
        role: "assistant",
        content: assistantContent,
        pending: false,
        metadata: assistantMetadata,
        planningSnapshot: assistantPlanningSnapshot,
      });
    }
    assistantId = null;
    assistantContent = "";
    assistantRawContent = "";
    assistantMetadata = undefined;
    assistantPlanningSnapshot = undefined;
  };

  for (const event of events) {
    if (
      (event.event === "run_started" || event.event === "assistant_message_started") &&
      !assistantId
    ) {
      assistantId = `assistant_replay_${seq}`;
      assistantContent = "";
      assistantRawContent = "";
    }
    if (event.event === "assistant_message_completed" || event.event === "assistant_message_replaced") {
      const content = getAssistantSnapshotContent(event);
      if (content !== undefined) {
        if (!assistantId) {
          assistantId = `assistant_replay_${seq}`;
        }
        assistantRawContent = content;
        assistantContent = stripTextFormToolCalls(content);
      }
    }
    if (event.event === "assistant_message_tombstoned") {
      assistantId = null;
      assistantContent = "";
      assistantRawContent = "";
      assistantMetadata = undefined;
      assistantPlanningSnapshot = undefined;
    }
    if (event.event === "llm_call_completed" || event.event === "run_completed") {
      const snapshot = parsePlanningSnapshot(event.data.planning_snapshot);
      if (snapshot) {
        assistantPlanningSnapshot = snapshot;
        if (assistantId) {
          applyPlanningToAssistantMessage(
            messages,
            assistantId,
            snapshot,
            assistantContent,
            assistantMetadata,
          );
        }
      }
    }
    if (event.event === "llm_call_completed") {
      const patch = parseLlmCompletedData(event.data);
      assistantMetadata = mergeAssistantMetadata(assistantMetadata, patch);
    }
    if (isTokenDelta(event)) {
      if (!assistantId) {
        assistantId = `assistant_replay_${seq}`;
      }
      assistantRawContent += getTokenDeltaText(event);
      assistantContent = stripTextFormToolCalls(assistantRawContent);
    }
    if (isToolCallStarted(event)) {
      if (!assistantId) {
        assistantId = `assistant_replay_${seq}`;
      }
      if (assistantContent.trim() || assistantPlanningSnapshot) {
        messages.push({
          id: assistantId,
          role: "assistant",
          content: assistantContent,
          pending: false,
          metadata: assistantMetadata,
          planningSnapshot: assistantPlanningSnapshot,
        });
        assistantContent = "";
        assistantRawContent = "";
        assistantMetadata = undefined;
        assistantPlanningSnapshot = undefined;
      }
      for (const tool of parseToolStatesFromEvent(event)) {
        if (PLANNING_TOOL_NAMES.has(tool.name)) {
          continue;
        }
        messages.push({
          id: `tool_replay_${tool.toolCallId}`,
          role: "tool",
          toolCallId: tool.toolCallId,
          name: tool.name,
          status: tool.status,
          argsSummary: tool.argsSummary,
          args: tool.args,
          resultPreview: tool.resultPreview,
          risk: tool.risk,
        });
      }
    }
    if (isToolCallCompleted(event)) {
      const snapshot = parsePlanningSnapshot(event.data.planning_snapshot);
      if (snapshot) {
        assistantPlanningSnapshot = snapshot;
        if (assistantId) {
          applyPlanningToAssistantMessage(
            messages,
            assistantId,
            snapshot,
            assistantContent,
            assistantMetadata,
          );
        }
      }
      for (const tool of parseToolStatesFromEvent(event)) {
        if (PLANNING_TOOL_NAMES.has(tool.name)) {
          continue;
        }
        const index = messages.findIndex(
          (item) => item.role === "tool" && item.toolCallId === tool.toolCallId,
        );
        if (index >= 0 && messages[index].role === "tool") {
          messages[index] = { ...messages[index], ...tool, status: tool.status };
        } else {
          messages.push({
            id: `tool_replay_${tool.toolCallId}`,
            role: "tool",
            toolCallId: tool.toolCallId,
            name: tool.name,
            status: tool.status,
            argsSummary: tool.argsSummary,
            args: tool.args,
            resultPreview: tool.resultPreview,
            risk: tool.risk,
            durationMs: tool.durationMs,
          });
        }
      }
    }
    if (isTerminalEvent(event)) {
      flushAssistant();
    }
    seq = event.seq;
  }
  flushAssistant();
  return messages;
}
