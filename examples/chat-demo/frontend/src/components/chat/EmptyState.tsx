import { useQuery } from "@tanstack/react-query";
import { Compass, Link2, ListChecks, Wrench } from "lucide-react";

import { fetchHealth } from "../../lib/api";
import { normalizeToolPreset, toolPresetLabel, useSettingsStore } from "../../store/settingsStore";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";

interface EmptyStateProps {
  onPromptSelect: (text: string) => void;
}

const STARTERS = [
  {
    icon: ListChecks,
    label: "Plan a change",
    prompt: "Please plan a small change to this demo and list the implementation steps.",
  },
  {
    icon: Wrench,
    label: "Exercise tools",
    prompt: "Show which tools are available in this session and when you would use each one.",
  },
  {
    icon: Link2,
    label: "Fetch a URL",
    prompt: "Fetch and summarize the main points from a URL I provide.",
  },
  {
    icon: Compass,
    label: "Explain runtime",
    prompt: "Explain how this chat demo streams events from the backend runtime.",
  },
];

export function EmptyState({ onPromptSelect }: EmptyStateProps) {
  const toolPreset = normalizeToolPreset(useSettingsStore((state) => state.toolPreset));
  const health = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 5000,
  });
  const providerName = health.data?.provider.provider_name ?? "checking";
  const providerStatus = health.isLoading
    ? "text-muted-foreground"
    : health.data?.provider.healthy
      ? "text-emerald-500"
      : "text-red-500";

  return (
    <div className="mx-auto flex h-full max-w-3xl items-start pt-4 sm:pt-6">
      <section className="w-full rounded-lg border border-border/80 bg-card/70 p-4 shadow-sm sm:p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-base font-semibold leading-tight">Start a conversation</h2>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
              Try a focused agent-driver task. Responses stream from the backend runtime, and
              tool choices stay tied to the current preset.
            </p>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <Badge variant="outline" className="capitalize text-xs">
              Tools: {toolPresetLabel(toolPreset)}
            </Badge>
            <Badge variant="outline" className="gap-1.5 text-xs">
              <span className={`h-1.5 w-1.5 rounded-full bg-current ${providerStatus}`} />
              {providerName}
            </Badge>
          </div>
        </div>
        <div className="mt-4 grid gap-2 sm:grid-cols-2">
          {STARTERS.map((starter) => {
            const Icon = starter.icon;
            return (
              <Button
                key={starter.label}
                type="button"
                variant="ghost"
                className="h-auto justify-start whitespace-normal rounded-md border border-border/70 bg-background/45 px-3 py-2.5 text-left hover:bg-secondary/70"
                onClick={() => onPromptSelect(starter.prompt)}
              >
                <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
                <span className="min-w-0 text-sm font-medium leading-5">{starter.label}</span>
              </Button>
            );
          })}
        </div>
      </section>
    </div>
  );
}
