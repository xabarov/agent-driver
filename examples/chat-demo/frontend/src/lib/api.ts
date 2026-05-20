import type { HealthResponse, ProviderResponse, ToolsResponse } from "../types/api";

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`request failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

export function fetchHealth(): Promise<HealthResponse> {
  return getJson<HealthResponse>("/api/health");
}

export function fetchProviders(): Promise<ProviderResponse> {
  return getJson<ProviderResponse>("/api/providers");
}

export function fetchTools(): Promise<ToolsResponse> {
  return getJson<ToolsResponse>("/api/tools");
}
