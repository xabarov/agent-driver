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
