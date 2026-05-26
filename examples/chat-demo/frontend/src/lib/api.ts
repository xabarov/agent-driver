import type {
  CreateSessionRequest,
  DeleteSessionResponse,
  HealthResponse,
  InterruptView,
  ProviderResponse,
  ModelsResponse,
  ReplayResponse,
  SessionDetailView,
  SessionsListResponse,
  ToolsResponse,
  WorkspaceImportResponse,
} from "../types/api";
import type { ToolPreset } from "../store/settingsStore";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    throw new Error(`request failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

export function fetchHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/api/health");
}

export function fetchProviders(): Promise<ProviderResponse> {
  return request<ProviderResponse>("/api/providers");
}

export function fetchModels(): Promise<ModelsResponse> {
  return request<ModelsResponse>("/api/models");
}

export function fetchTools(preset?: ToolPreset, sessionId?: string): Promise<ToolsResponse> {
  const params = new URLSearchParams();
  if (preset) {
    params.set("preset", preset);
  }
  if (sessionId) {
    params.set("session_id", sessionId);
  }
  const query = params.toString();
  return request<ToolsResponse>(`/api/tools${query ? `?${query}` : ""}`);
}

export function importSampleWorkspace(sessionId: string): Promise<WorkspaceImportResponse> {
  const params = new URLSearchParams({ session_id: sessionId });
  return request<WorkspaceImportResponse>(`/api/workspace/sample?${params.toString()}`, {
    method: "POST",
  });
}

export function cancelRun(runId: string): Promise<{ ok: boolean; run_id: string; cancelled: boolean }> {
  return request(`/api/chat/runs/${runId}/cancel`, { method: "POST" });
}

export function fetchInterrupt(runId: string): Promise<InterruptView> {
  return request<InterruptView>(`/api/chat/runs/${runId}/interrupt`);
}

export function fetchReplay(sessionId: string, runId: string): Promise<ReplayResponse> {
  return request<ReplayResponse>(
    `/api/sessions/${sessionId}/replay?run_id=${encodeURIComponent(runId)}`,
  );
}

export function listSessions(): Promise<SessionsListResponse> {
  return request<SessionsListResponse>("/api/sessions");
}

export function getSession(sessionId: string): Promise<SessionDetailView> {
  return request<SessionDetailView>(`/api/sessions/${sessionId}`);
}

export function createSession(body: CreateSessionRequest): Promise<SessionDetailView> {
  return request<SessionDetailView>("/api/sessions", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function deleteSession(sessionId: string): Promise<DeleteSessionResponse> {
  return request<DeleteSessionResponse>(`/api/sessions/${sessionId}`, {
    method: "DELETE",
  });
}
