import { fetchEventSource } from "@microsoft/fetch-event-source";

import type { RunStreamEvent } from "./events";

interface StreamMeta {
  sessionId?: string;
  runId?: string;
}

export interface StartChatStreamOptions {
  message: string;
  sessionId?: string;
  signal?: AbortSignal;
  lastEventId?: string;
  onEvent: (event: RunStreamEvent) => void;
  onMeta?: (meta: StreamMeta) => void;
}

export async function startChatStream(opts: StartChatStreamOptions): Promise<void> {
  await fetchEventSource("/api/chat/messages", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(opts.lastEventId ? { "Last-Event-ID": opts.lastEventId } : {}),
    },
    body: JSON.stringify({
      message: opts.message,
      session_id: opts.sessionId,
    }),
    signal: opts.signal,
    openWhenHidden: true,
    async onopen(response) {
      if (
        !response.ok ||
        !response.headers.get("content-type")?.startsWith("text/event-stream")
      ) {
        throw new Error(`bad sse response: ${response.status}`);
      }
      opts.onMeta?.({
        sessionId: response.headers.get("x-session-id") ?? undefined,
        runId: response.headers.get("x-run-id") ?? undefined,
      });
    },
    onmessage(message) {
      if (!message.data) {
        return;
      }
      const payload = JSON.parse(message.data) as RunStreamEvent;
      opts.onEvent(payload);
    },
  });
}
