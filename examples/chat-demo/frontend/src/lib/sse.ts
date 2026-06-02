import { fetchEventSource } from "@microsoft/fetch-event-source";

import type {
  HardResearchOptions,
  ProfileSource,
  ResearchMode,
  ResearchProfile,
  ToolPreset,
} from "../store/settingsStore";
import type { RunStreamEvent } from "./events";

export type ChatRequestToolPreset = ToolPreset | "deep_research";

interface StreamMeta {
  sessionId?: string;
  runId?: string;
}

class FatalSseError extends Error {}

export interface StartChatStreamOptions {
  message: string;
  sessionId?: string;
  toolPreset?: ChatRequestToolPreset;
  model?: string;
  retryFromRunId?: string;
  clientRequestId?: string;
  researchDepth?: "deep_parallel_research";
  researchMode?: ResearchMode;
  researchProfile?: ResearchProfile;
  profileSource?: ProfileSource;
  hardResearchOptions?: HardResearchOptions;
  signal?: AbortSignal;
  lastEventId?: string;
  onEvent: (event: RunStreamEvent) => void;
  onMeta?: (meta: StreamMeta) => void;
}

export interface ResumeRunStreamOptions {
  runId: string;
  interruptId: string;
  action: string;
  toolPreset?: ToolPreset;
  model?: string;
  editedToolArgs?: Record<string, unknown>;
  message?: string;
  signal?: AbortSignal;
  lastEventId?: string;
  onEvent: (event: RunStreamEvent) => void;
  allowReconnect?: boolean;
}

async function consumeSse(
  url: string,
  init: RequestInit,
  onEvent: (event: RunStreamEvent) => void,
  onMeta?: (meta: StreamMeta) => void,
  allowReconnect = false,
): Promise<void> {
  await fetchEventSource(url, {
    ...init,
    headers: init.headers as Record<string, string> | undefined,
    openWhenHidden: true,
    async onopen(response) {
      if (
        !response.ok ||
        !response.headers.get("content-type")?.startsWith("text/event-stream")
      ) {
        throw new FatalSseError(`bad sse response: ${response.status}`);
      }
      onMeta?.({
        sessionId: response.headers.get("x-session-id") ?? undefined,
        runId: response.headers.get("x-run-id") ?? undefined,
      });
    },
    onmessage(message) {
      if (!message.data) {
        return;
      }
      const payload = JSON.parse(message.data) as RunStreamEvent;
      onEvent(payload);
    },
    onerror(error) {
      if (!allowReconnect || error instanceof FatalSseError) {
        throw error;
      }
    },
  });
}

export async function startChatStream(opts: StartChatStreamOptions): Promise<void> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (opts.lastEventId) {
    headers["Last-Event-ID"] = opts.lastEventId;
  }
  await consumeSse(
    "/api/chat/messages",
    {
      method: "POST",
      headers,
      body: JSON.stringify({
        message: opts.message,
        session_id: opts.sessionId,
        tool_preset: opts.toolPreset,
        model: opts.model,
        retry_from_run_id: opts.retryFromRunId,
        client_request_id: opts.clientRequestId,
        research_depth: opts.researchDepth,
        research_mode: opts.researchMode,
        research_profile: opts.researchProfile,
        profile_source: opts.profileSource,
        hard_options: opts.hardResearchOptions
          ? {
              allow_pdf_read: opts.hardResearchOptions.allowPdfRead,
              allow_browser_read: opts.hardResearchOptions.allowBrowserRead,
              allow_browser_action: opts.hardResearchOptions.allowBrowserAction,
            }
          : undefined,
      }),
      signal: opts.signal,
    },
    opts.onEvent,
    opts.onMeta,
    Boolean(opts.clientRequestId),
  );
}

export async function resumeRunStream(opts: ResumeRunStreamOptions): Promise<void> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (opts.lastEventId) {
    headers["Last-Event-ID"] = opts.lastEventId;
  }
  await consumeSse(
    `/api/chat/runs/${opts.runId}/resume`,
    {
      method: "POST",
      headers,
      body: JSON.stringify({
        interrupt_id: opts.interruptId,
        action: opts.action,
        tool_preset: opts.toolPreset,
        model: opts.model,
        edited_tool_args: opts.editedToolArgs,
        message: opts.message,
      }),
      signal: opts.signal,
    },
    opts.onEvent,
    undefined,
    false,
  );
}
