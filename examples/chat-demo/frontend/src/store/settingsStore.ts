import { create } from "zustand";
import { persist } from "zustand/middleware";

export type ToolPreset = "off" | "web_search" | "web_fetch" | "web";
export type ResearchDepth = "standard" | "deep_parallel_research";
export type ResearchMode = "chat" | "web" | "deep";
export type ResearchProfile = "light" | "medium" | "hard";
export type ProfileSource = "user_selected" | "auto_suggested" | "backend_classified" | "scenario_forced";

export interface HardResearchOptions {
  allowPdfRead: boolean;
  allowBrowserRead: boolean;
  allowBrowserAction: boolean;
}

export const DEFAULT_HARD_RESEARCH_OPTIONS: HardResearchOptions = {
  allowPdfRead: true,
  allowBrowserRead: false,
  allowBrowserAction: false,
};

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
  researchDepth: ResearchDepth;
  researchMode: ResearchMode;
  researchProfile: ResearchProfile;
  profileSource: ProfileSource;
  hardResearchOptions: HardResearchOptions;
  model: string;
  setToolPreset: (preset: ToolPreset) => void;
  setResearchDepth: (depth: ResearchDepth) => void;
  setResearchMode: (mode: ResearchMode) => void;
  setResearchProfile: (profile: ResearchProfile) => void;
  setProfileSource: (source: ProfileSource) => void;
  setHardResearchOptions: (options: Partial<HardResearchOptions>) => void;
  setModel: (model: string) => void;
}

function normalizeResearchMode(value: unknown): ResearchMode {
  if (value === "chat" || value === "web" || value === "deep") {
    return value;
  }
  return "web";
}

function normalizeResearchProfile(value: unknown): ResearchProfile {
  if (value === "light" || value === "medium" || value === "hard") {
    return value;
  }
  return "medium";
}

function normalizeProfileSource(value: unknown): ProfileSource {
  if (
    value === "user_selected" ||
    value === "auto_suggested" ||
    value === "backend_classified" ||
    value === "scenario_forced"
  ) {
    return value;
  }
  return "user_selected";
}

function normalizeHardResearchOptions(value: unknown): HardResearchOptions {
  if (!value || typeof value !== "object") {
    return DEFAULT_HARD_RESEARCH_OPTIONS;
  }
  const options = value as Partial<HardResearchOptions>;
  return {
    allowPdfRead: options.allowPdfRead ?? DEFAULT_HARD_RESEARCH_OPTIONS.allowPdfRead,
    allowBrowserRead:
      options.allowBrowserRead ?? DEFAULT_HARD_RESEARCH_OPTIONS.allowBrowserRead,
    allowBrowserAction:
      options.allowBrowserAction ?? DEFAULT_HARD_RESEARCH_OPTIONS.allowBrowserAction,
  };
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      toolPreset: "web",
      researchDepth: "standard",
      researchMode: "web",
      researchProfile: "medium",
      profileSource: "user_selected",
      hardResearchOptions: DEFAULT_HARD_RESEARCH_OPTIONS,
      model: "",
      setToolPreset: (toolPreset) => set({ toolPreset: normalizeToolPreset(toolPreset) }),
      setResearchDepth: (researchDepth) => set({ researchDepth }),
      setResearchMode: (researchMode) =>
        set({
          researchMode,
          researchDepth: researchMode === "deep" ? "deep_parallel_research" : "standard",
          profileSource: "user_selected",
        }),
      setResearchProfile: (researchProfile) =>
        set({ researchProfile, profileSource: "user_selected" }),
      setProfileSource: (profileSource) => set({ profileSource }),
      setHardResearchOptions: (options) =>
        set((state) => ({
          hardResearchOptions: normalizeHardResearchOptions({
            ...state.hardResearchOptions,
            ...options,
          }),
        })),
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
          researchDepth:
            state.researchDepth === "deep_parallel_research"
              ? "deep_parallel_research"
              : "standard",
          researchMode:
            state.researchDepth === "deep_parallel_research"
              ? "deep"
              : normalizeResearchMode(state.researchMode),
          researchProfile: normalizeResearchProfile(state.researchProfile),
          profileSource: normalizeProfileSource(state.profileSource),
          hardResearchOptions: normalizeHardResearchOptions(state.hardResearchOptions),
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
          researchDepth:
            state.researchDepth === "deep_parallel_research"
              ? "deep_parallel_research"
              : "standard",
          researchMode:
            state.researchDepth === "deep_parallel_research"
              ? "deep"
              : normalizeResearchMode(state.researchMode),
          researchProfile: normalizeResearchProfile(state.researchProfile),
          profileSource: normalizeProfileSource(state.profileSource),
          hardResearchOptions: normalizeHardResearchOptions(state.hardResearchOptions),
        };
      },
    },
  ),
);
