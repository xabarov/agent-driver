import { useQuery } from "@tanstack/react-query";

import { fetchTools } from "./api";
import { normalizeToolPreset, type ToolPreset } from "../store/settingsStore";

export function toolsQueryKey(preset: ToolPreset, sessionId?: string) {
  return ["tools", normalizeToolPreset(preset), sessionId ?? "no-session"] as const;
}

export function useToolsForPreset(preset: ToolPreset, sessionId?: string) {
  const normalized = normalizeToolPreset(preset);
  return useQuery({
    queryKey: toolsQueryKey(normalized, sessionId),
    queryFn: () => fetchTools(normalized, sessionId),
    staleTime: 30_000,
  });
}

export const PRESET_HINTS: Record<ToolPreset, string> = {
  off: "Web tools disabled. Planning stays available to the agent.",
  web_search: "Search the web for current information.",
  web_fetch: "Fetch and read content from URLs.",
  web: "Search the web and fetch URL content.",
};
