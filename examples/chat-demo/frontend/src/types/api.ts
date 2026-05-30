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

export interface WorkspaceStatusView {
  mode: string;
  root: string;
  sessionId: string | null;
  exists: boolean;
  fileCount: number;
  sampleAvailable: boolean;
}

export interface ToolsResponse {
  tools: ToolManifestView[];
  workspace: WorkspaceStatusView;
}

export interface WorkspaceImportResponse {
  ok: boolean;
  files: string[];
  workspace: WorkspaceStatusView;
}

export interface AssistantMessageMetadataView {
  promptTokens?: number;
  completionTokens?: number;
  totalTokens?: number;
  durationMs?: number;
  tokensPerSecond?: number;
  costUsd?: number;
  model?: string;
  provider?: string;
  estimated?: boolean;
  source_evidence?: unknown[];
  sourceEvidence?: unknown[];
  steering_controls?: SteeringControlView[];
  steeringControls?: SteeringControlView[];
  compaction?: CompactionNoticeView;
}

export interface CompactionNoticeView {
  compaction_id?: string;
  compactionId?: string;
  status?: string;
  mode?: string;
  reason?: string;
  failure_kind?: string;
  failureKind?: string;
  summarized_message_count?: number;
  summarizedMessageCount?: number;
  attempts?: number;
}

export interface SteeringControlView {
  queue_id?: string;
  queueId?: string;
  control_id?: string;
  controlId?: string;
  kind?: string;
  priority?: string;
  status?: string;
  payload?: Record<string, unknown>;
  source?: string;
  created_at?: string;
  updated_at?: string;
}

export interface SessionMessageView {
  role: string;
  content: string;
  metadata?: AssistantMessageMetadataView | null;
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
  metadata_by_run?: Record<string, AssistantMessageMetadataView>;
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

export interface ChatControlRequest {
  kind: string;
  priority?: "now" | "next" | "later";
  payload?: Record<string, unknown>;
  thread_id?: string;
  agent_id?: string;
  dedupe_key?: string;
}

export interface ChatControlResponse {
  ok: boolean;
  control_id?: string | null;
  queue_id?: string | null;
  error?: string | null;
}

export interface ReplayResponse {
  run_id: string;
  events: Array<Record<string, unknown>>;
}

export interface RunTraceSummaryResponse {
  run_id: string;
  verdict: string;
  terminal_event?: string | null;
  compaction?: {
    attempts?: number;
    started?: number;
    successful?: number;
    failed?: number;
    skipped?: number;
    modes?: string[];
    circuit_breaker_open?: boolean;
    latest?: Record<string, unknown> | null;
  };
}

export interface ModelView {
  id: string;
  name: string | null;
  description: string | null;
  context_length: number | null;
}

export interface ModelsResponse {
  provider: string;
  models: ModelView[];
}
