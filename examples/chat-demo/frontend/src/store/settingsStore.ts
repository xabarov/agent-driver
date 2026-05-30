import { create } from "zustand";
import { persist } from "zustand/middleware";

export type ToolPreset = "off" | "web_search" | "web_fetch" | "web";

export function normalizeToolPreset(value: unknown): ToolPreset {
  if (
    value === "web" ||
    value === "web_search" ||
    value === "web_fetch" ||
    value === "off"
  ) {
    return value;
  }
  if (
    value === "agents" ||
    value === "safe" ||
    value === "workspace" ||
    value === "dev" ||
    value === "all"
  ) {
    return "web";
  }
  return "web";
}

export function toolPresetLabel(preset: ToolPreset): string {
  if (preset === "web") {
    return "Web";
  }
  if (preset === "web_search") {
    return "Search";
  }
  if (preset === "web_fetch") {
    return "Fetch";
  }
  return "Off";
}

interface SettingsState {
  toolPreset: ToolPreset;
  model: string;
  setToolPreset: (preset: ToolPreset) => void;
  setModel: (model: string) => void;
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      toolPreset: "web",
      model: "",
      setToolPreset: (toolPreset) => set({ toolPreset: normalizeToolPreset(toolPreset) }),
      setModel: (model) => set({ model }),
    }),
    {
      name: "chat-demo-settings",
      merge: (persisted, current) => {
        if (!persisted || typeof persisted !== "object") {
          return current;
        }
        const state = persisted as Partial<SettingsState>;
        return {
          ...current,
          ...state,
          toolPreset: normalizeToolPreset(state.toolPreset),
        };
      },
      migrate: (persisted) => {
        if (!persisted || typeof persisted !== "object") {
          return persisted;
        }
        const state = persisted as Partial<SettingsState>;
        return {
          ...state,
          toolPreset: normalizeToolPreset(state.toolPreset),
        };
      },
    },
  ),
);
