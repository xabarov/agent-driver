import { create } from "zustand";
import { persist } from "zustand/middleware";

export type ToolPreset = "off" | "safe" | "dev" | "all";

interface SettingsState {
  toolPreset: ToolPreset;
  model: string;
  setToolPreset: (preset: ToolPreset) => void;
  setModel: (model: string) => void;
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      toolPreset: "safe",
      model: "",
      setToolPreset: (toolPreset) => set({ toolPreset }),
      setModel: (model) => set({ model }),
    }),
    { name: "chat-demo-settings" },
  ),
);
