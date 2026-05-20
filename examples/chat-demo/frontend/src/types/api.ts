export interface ProviderStatusView {
  provider_name: string;
  provider_kind: string;
  healthy: boolean;
  configured: boolean;
  latency_ms: number | null;
  avg_latency_ms: number | null;
  request_count: number;
  error_count: number;
}

export interface HealthResponse {
  ok: boolean;
  store_kind: string;
  provider: ProviderStatusView;
}

export interface ProviderResponse {
  name: string;
  model: string | null;
  base_url: string | null;
  status: ProviderStatusView;
}

export interface ToolManifestView {
  name: string;
  description: string;
  risk: string;
  sideEffect: string;
  approvalMode: string;
}

export interface ToolsResponse {
  tools: ToolManifestView[];
}

export interface SessionMessageView {
  role: string;
  content: string;
}

export interface SessionSummaryView {
  session_id: string;
  thread_id: string;
  title: string;
  updated_at: string;
  runs_count: number;
}

export interface SessionDetailView {
  session_id: string;
  thread_id: string;
  title: string;
  run_ids: string[];
  transcript: SessionMessageView[];
  created_at: string;
  updated_at: string;
}

export interface SessionsListResponse {
  sessions: SessionSummaryView[];
}

export interface CreateSessionRequest {
  title?: string;
}

export interface DeleteSessionResponse {
  ok: boolean;
}

export interface InterruptView {
  run_id: string;
  interrupt_id: string;
  reason: string;
  title?: string | null;
  description?: string | null;
  proposed_action: Record<string, unknown>;
  allowed_actions: string[];
}

export interface ResumeRequest {
  interrupt_id: string;
  action: string;
  tool_preset?: string;
  edited_tool_args?: Record<string, unknown>;
  message?: string;
}

export interface ReplayResponse {
  run_id: string;
  events: Array<Record<string, unknown>>;
}
