import {
  mergeAssistantMetadata,
  parseLlmCompletedData,
  type AssistantMessageMetadata,
} from "./messageMetadata";
import { parsePlanningSnapshot } from "./planning";
import { stripTextFormToolCalls } from "./stripToolCalls";
import type { ChatMessage, ToolCallStatus } from "../store/chatStore";
import type { PlanningSnapshot } from "./planning";

const CONTROL_TOOL_NAMES = new Set([
  "todo_write",
  "planning_state_update",
  "ask_user_question",
]);

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

export interface ParsedSteeringEvent {
  seq: number;
  event: string;
  queueId: string;
  controlId?: string;
  kind?: string;
  priority?: string;
}

export interface ParsedSubagentChildRun {
  taskId: string;
  subagentRunId?: string;
  childRunId?: string;
  status: "spawned" | "running" | "completed" | "failed" | "cancelled";
  description?: string;
  outputPreview?: string;
  usedTools?: string[];
  warning?: string;
}

export interface ParsedSubagentLifecycleEvent {
  seq: number;
  event:
    | "subagent_group_started"
    | "subagent_group_joined"
    | "subagent_group_join_waiting"
    | "subagent_group_failed"
    | "subagent_spawned"
    | "subagent_started"
    | "subagent_completed";
  groupId?: string;
  joinState?: string;
  childRun?: ParsedSubagentChildRun;
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

function summarizePrimitive(value: unknown): string | undefined {
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return undefined;
}

function summarizeArgs(args: unknown): string | undefined {
  if (!args || typeof args !== "object" || Array.isArray(args)) {
    return undefined;
  }

  const record = args as Record<string, unknown>;
  const preferred: Array<[string, string]> = [
    ["query", "query"],
    ["url", "url"],
    ["path", "path"],
    ["pattern", "pattern"],
    ["max_results", "max results"],
    ["max_chars", "max chars"],
  ];
  const parts = preferred
    .map(([key, label]) => {
      const value = summarizePrimitive(record[key]);
      return value ? `${label}: ${value}` : undefined;
    })
    .filter((value): value is string => Boolean(value));

  if (!parts.length) {
    for (const [key, rawValue] of Object.entries(record)) {
      const value = summarizePrimitive(rawValue);
      if (value) {
        parts.push(`${key}: ${value}`);
      }
      if (parts.length >= 3) {
        break;
      }
    }
  }

  const text = parts.join(" · ");
  return text.length > 160 ? `${text.slice(0, 157)}...` : text || undefined;
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

export function parseSteeringEvents(
  events: RunStreamEvent<Record<string, unknown>>[],
): ParsedSteeringEvent[] {
  return events
    .filter((event) =>
      [
        "control_requested",
        "command_queued",
        "command_dequeued",
        "command_cancelled",
        "control_applied",
      ].includes(event.event),
    )
    .map((event) => {
      const queueId = event.data.queue_id;
      const controlId = event.data.control_id;
      const kind = event.data.kind;
      const priority = event.data.priority;
      return {
        seq: event.seq,
        event: event.event,
        queueId: typeof queueId === "string" ? queueId : "",
        controlId: typeof controlId === "string" ? controlId : undefined,
        kind: typeof kind === "string" ? kind : undefined,
        priority: typeof priority === "string" ? priority : undefined,
      };
    })
    .filter((event) => event.queueId);
}

function lifecycleText(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function lifecycleTextList(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) {
    return undefined;
  }
  const items = value.filter(
    (item): item is string => typeof item === "string" && item.trim().length > 0,
  );
  return items.length ? items : undefined;
}

function lifecycleStatus(
  eventName: ParsedSubagentLifecycleEvent["event"],
  rawStatus: unknown,
): ParsedSubagentChildRun["status"] {
  const status = typeof rawStatus === "string" ? rawStatus.toLowerCase() : "";
  if (status === "failed" || status === "error" || status === "timeout") {
    return "failed";
  }
  if (status === "cancelled" || status === "canceled" || status === "stopped") {
    return "cancelled";
  }
  if (status === "completed" || status === "done") {
    return "completed";
  }
  if (eventName === "subagent_spawned") {
    return "spawned";
  }
  if (eventName === "subagent_completed") {
    return "completed";
  }
  return "running";
}

export function parseSubagentLifecycleEvent(
  event: RunStreamEvent<Record<string, unknown>>,
): ParsedSubagentLifecycleEvent | undefined {
  if (
    event.event !== "subagent_group_started" &&
    event.event !== "subagent_group_joined" &&
    event.event !== "subagent_group_join_waiting" &&
    event.event !== "subagent_group_failed" &&
    event.event !== "subagent_spawned" &&
    event.event !== "subagent_started" &&
    event.event !== "subagent_completed"
  ) {
    return undefined;
  }
  const eventName = event.event;
  const taskId =
    lifecycleText(event.data.task_id) ??
    lifecycleText(event.data.subagent_run_id) ??
    lifecycleText(event.data.child_run_id);
  return {
    seq: event.seq,
    event: eventName,
    groupId: lifecycleText(event.data.group_id),
    joinState: lifecycleText(event.data.join_state),
    childRun: taskId
      ? {
          taskId,
          subagentRunId: lifecycleText(event.data.subagent_run_id),
          childRunId: lifecycleText(event.data.child_run_id),
          status: lifecycleStatus(eventName, event.data.status),
          description: lifecycleText(event.data.description),
          outputPreview:
            lifecycleText(event.data.output_preview) ??
            lifecycleText(event.data.summary) ??
            lifecycleText(event.data.result_summary),
          usedTools:
            lifecycleTextList(event.data.used_tools) ??
            lifecycleTextList(event.data.tool_names),
          warning:
            lifecycleText(event.data.warning) ??
            lifecycleText(event.data.error) ??
            lifecycleText(event.data.reason),
        }
      : undefined,
  };
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

function applySubagentLifecycleToLatestTool(
  messages: ChatMessage[],
  lifecycle: ParsedSubagentLifecycleEvent,
): void {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message?.role !== "tool" || message.name !== "agent_tool") {
      continue;
    }
    const current = message.subagent ?? {};
    const nextChildren = [...(current.childRuns ?? [])];
    if (lifecycle.childRun) {
      const childIndex = nextChildren.findIndex(
        (child) =>
          child.taskId === lifecycle.childRun?.taskId ||
          Boolean(
            lifecycle.childRun?.subagentRunId &&
              child.subagentRunId === lifecycle.childRun.subagentRunId,
          ) ||
          Boolean(
            lifecycle.childRun?.childRunId &&
              child.childRunId === lifecycle.childRun.childRunId,
          ),
      );
      if (childIndex < 0) {
        nextChildren.push(lifecycle.childRun);
      } else {
        nextChildren[childIndex] = {
          ...nextChildren[childIndex],
          ...lifecycle.childRun,
        };
      }
    }
    let groupStatus = current.groupStatus;
    if (lifecycle.event === "subagent_group_started") {
      groupStatus = "running";
    } else if (lifecycle.event === "subagent_group_joined") {
      groupStatus = "joined";
    } else if (lifecycle.event === "subagent_group_join_waiting") {
      groupStatus = "waiting";
    } else if (lifecycle.event === "subagent_group_failed") {
      groupStatus = "failed";
    }
    messages[index] = {
      ...message,
      subagent: {
        ...current,
        groupId: lifecycle.groupId ?? current.groupId,
        groupStatus,
        joinState: lifecycle.joinState ?? current.joinState,
        childRuns: nextChildren,
      },
    };
    return;
  }
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
      if (assistantId && assistantPlanningSnapshot) {
        messages.push({
          id: assistantId,
          role: "assistant",
          content: "",
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
        if (CONTROL_TOOL_NAMES.has(tool.name)) {
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
        if (CONTROL_TOOL_NAMES.has(tool.name)) {
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
    const subagentLifecycle = parseSubagentLifecycleEvent(event);
    if (subagentLifecycle) {
      applySubagentLifecycleToLatestTool(messages, subagentLifecycle);
    }
    if (isTerminalEvent(event)) {
      flushAssistant();
    }
    seq = event.seq;
  }
  flushAssistant();
  return messages;
}
