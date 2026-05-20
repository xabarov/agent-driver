import { useQuery } from "@tanstack/react-query";

import { fetchTools } from "./api";
import type { ToolPreset } from "../store/settingsStore";

export function toolsQueryKey(preset: ToolPreset) {
  return ["tools", preset] as const;
}

export function useToolsForPreset(preset: ToolPreset) {
  return useQuery({
    queryKey: toolsQueryKey(preset),
    queryFn: () => fetchTools(preset),
    staleTime: 30_000,
  });
}

export const PRESET_HINTS: Record<ToolPreset, string> = {
  off: "No tools — text-only replies.",
  safe: "Read-only filesystem, web search/fetch, planning.",
  dev: "Safe tools plus shell and filesystem write.",
  all: "Full tool surface including dangerous tools.",
};
