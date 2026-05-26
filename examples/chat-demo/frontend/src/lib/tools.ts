import { useQuery } from "@tanstack/react-query";

import { fetchTools } from "./api";
import type { ToolPreset } from "../store/settingsStore";

export function toolsQueryKey(preset: ToolPreset, sessionId?: string) {
  return ["tools", preset, sessionId ?? "no-session"] as const;
}

export function useToolsForPreset(preset: ToolPreset, sessionId?: string) {
  return useQuery({
    queryKey: toolsQueryKey(preset, sessionId),
    queryFn: () => fetchTools(preset, sessionId),
    staleTime: 30_000,
  });
}

export const PRESET_HINTS: Record<ToolPreset, string> = {
  off: "No tools — text-only replies.",
  safe: "Web search/fetch and planning only — no filesystem access.",
  workspace: "Safe tools plus read-only search over this session workspace.",
  dev: "Workspace read/write tools plus governed shell.",
  all: "Full tool surface including dangerous tools.",
};
