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

export interface SkillManifestView {
  name: string;
  description: string;
  whenToUse?: string | null;
  tags: string[];
  allowedTools: string[];
  trusted: boolean;
  source: string;
  skillDir: string;
  path: string;
  supportingFiles: Array<Record<string, unknown>>;
  safetyWarnings: string[];
  digest: string;
}

export interface SkillsListResponse {
  skills: SkillManifestView[];
  uploadEnabled: boolean;
}

export interface SkillViewResponse {
  skill: SkillManifestView;
  content: string;
  contentKind: string;
  contentPath: string;
  relativeFile?: string | null;
  truncated: boolean;
  skillInvocation: Record<string, unknown>;
}

export interface SkillUploadRequest {
  name: string;
  content: string;
}

export interface SkillUploadResponse {
  skill: SkillManifestView;
}

export interface WorkspaceImportResponse {
  ok: boolean;
  files: string[];
  workspace: WorkspaceStatusView;
}

export interface WorkspaceArtifactView {
  path: string;
  kind: string;
  sizeBytes: number;
  modifiedAt: string;
}

export interface WorkspaceArtifactsResponse {
  ok: boolean;
  sessionId: string;
  artifacts: WorkspaceArtifactView[];
}

export interface WorkspaceArtifactPreviewResponse {
  ok: boolean;
  sessionId: string;
  path: string;
  kind: string;
  sizeBytes: number;
  content: string;
  truncated: boolean;
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
  deep_research_artifacts?: Record<string, unknown>;
  deepResearchArtifacts?: Record<string, unknown>;
  steering_controls?: SteeringControlView[];
  steeringControls?: SteeringControlView[];
  research_mode?: string;
  researchMode?: string;
  research_profile?: string;
  researchProfile?: string;
  profile_source?: string;
  profileSource?: string;
  hard_options?: Record<string, unknown>;
  hardOptions?: Record<string, unknown>;
  research_depth?: string;
  researchDepth?: string;
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

export interface DeepResearchArtifactState {
  path: string;
  kind: string;
  sizeBytes: number;
  modifiedAt?: string | null;
  lifecycle: string;
  previewAvailable: boolean;
}

export interface DeepResearchArtifactsState {
  report?: DeepResearchArtifactState | null;
  sourceLedger?: DeepResearchArtifactState | null;
  claims?: DeepResearchArtifactState | null;
}

export interface DeepResearchSourceCounts {
  verified: number;
  candidates: number;
  blocked: number;
  failed: number;
  distinctDomains: number;
}

export interface DeepResearchTodoState {
  done: number;
  total: number;
  current?: string | null;
  stale: boolean;
}

export interface DeepResearchSubagentState {
  totalChildren: number;
  runningChildren: number;
  completedChildren: number;
  failedChildren: number;
  duplicatedQueries: number;
}

export interface DeepResearchMetricsState {
  promptTokens?: number | null;
  completionTokens?: number | null;
  totalTokens?: number | null;
  webSearchCount: number;
  webFetchCount: number;
  reportFullWriteCount: number;
  reportPatchCount: number;
  longChatBeforeReportChars: number;
}

export interface DeepResearchTraceState {
  runId: string;
  verdict?: string | null;
  terminalEvent?: string | null;
  failureFlags: string[];
}

export interface DeepResearchViewState {
  runId: string;
  sessionId?: string | null;
  researchMode: string;
  profile: string;
  profileSource: string;
  phase: string;
  phaseSource: string;
  readiness: string;
  todos: DeepResearchTodoState;
  artifacts: DeepResearchArtifactsState;
  sources: DeepResearchSourceCounts;
  subagents: DeepResearchSubagentState;
  metrics: DeepResearchMetricsState;
  warnings: string[];
  trace: DeepResearchTraceState;
}

export interface ModelView {
  id: string;
  name: string | null;
  description: string | null;
  context_length: number | null;
  capability_profile?: Record<string, unknown> | null;
}

export interface ModelsResponse {
  provider: string;
  models: ModelView[];
}
