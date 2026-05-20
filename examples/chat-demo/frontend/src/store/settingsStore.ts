import { create } from "zustand";
import { persist } from "zustand/middleware";

export type ToolPreset = "off" | "safe" | "dev" | "all";

interface SettingsState {
  toolPreset: ToolPreset;
  setToolPreset: (preset: ToolPreset) => void;
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      toolPreset: "safe",
      setToolPreset: (toolPreset) => set({ toolPreset }),
    }),
    { name: "chat-demo-settings" },
  ),
);
