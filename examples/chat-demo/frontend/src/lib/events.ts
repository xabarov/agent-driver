export type StreamEventName =
  | "run_started"
  | "run_resumed"
  | "llm_call_started"
  | "llm_call_completed"
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

export function isTokenDelta(
  event: RunStreamEvent<any>,
): event is RunStreamEvent<TokenDeltaData> {
  return event.event === "token_delta";
}

export function isTerminalEvent(event: RunStreamEvent<any>): boolean {
  return (
    event.event === "run_completed" ||
    event.event === "run_failed" ||
    event.event === "run_cancelled"
  );
}
