import { create } from "zustand";
import { persist } from "zustand/middleware";

export type ToolPreset = "off" | "safe" | "workspace" | "dev" | "all";

interface SettingsState {
  toolPreset: ToolPreset;
  forcePlanning: boolean;
  model: string;
  setToolPreset: (preset: ToolPreset) => void;
  setForcePlanning: (enabled: boolean) => void;
  setModel: (model: string) => void;
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      toolPreset: "safe",
      forcePlanning: false,
      model: "",
      setToolPreset: (toolPreset) => set({ toolPreset }),
      setForcePlanning: (forcePlanning) => set({ forcePlanning }),
      setModel: (model) => set({ model }),
    }),
    { name: "chat-demo-settings" },
  ),
);
