import { useQuery } from "@tanstack/react-query";
import { Circle, MessageSquare, Moon, Radio, Sun } from "lucide-react";

import { fetchHealth } from "../../lib/api";
import { useChatStore } from "../../store/chatStore";
import { ModelPicker } from "./ModelPicker";
import { Badge } from "../ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "../ui/tooltip";
import { useThemeMode } from "./ThemeProvider";

function shortRunId(runId: string): string {
  return runId.length > 12 ? `${runId.slice(0, 8)}...${runId.slice(-4)}` : runId;
}

export function Header() {
  const { theme, toggleTheme } = useThemeMode();
  const runId = useChatStore((state) => state.runId);
  const runCount = useChatStore(
    (state) =>
      new Set(
        state.messages
          .filter((message) => message.role === "assistant" && message.runId)
          .map((message) => (message.role === "assistant" ? message.runId : undefined)),
      ).size,
  );
  const lastAssistantMetadata = useChatStore((state) => {
    for (let index = state.messages.length - 1; index >= 0; index -= 1) {
      const message = state.messages[index];
      if (message?.role === "assistant" && message.metadata) {
        return message.metadata;
      }
    }
    return undefined;
  });
  const health = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 5000,
  });

  const provider = health.data?.provider;
  const providerName = provider?.provider_name ?? (health.isError ? "offline" : "checking");
  const providerLabel = health.isLoading
    ? "checking"
    : health.isError
      ? "offline"
      : provider?.configured === false
        ? "not configured"
        : provider?.healthy
          ? providerName
          : "offline";
  const providerTone = health.isLoading
    ? "fill-muted-foreground text-muted-foreground"
    : health.isError || provider?.healthy === false
      ? "fill-red-500 text-red-500"
      : "fill-emerald-500 text-emerald-500";
  const providerTooltip = health.isLoading
    ? "Checking provider health..."
    : health.isError
      ? "Provider health request failed."
      : provider
        ? `${provider.provider_kind} · ${provider.configured ? "configured" : "not configured"} · ${provider.healthy ? "healthy" : "unhealthy"}${
            provider.latency_ms != null ? ` · ${Math.round(provider.latency_ms)}ms` : ""
          } · ${provider.request_count} requests · ${provider.error_count} errors`
        : "Provider status unavailable.";

  return (
    <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex min-w-0 items-center gap-2">
        <MessageSquare className="h-5 w-5 text-primary" />
        <div className="min-w-0">
          <h1 className="text-base font-semibold leading-tight">Chat</h1>
          <p className="truncate text-xs text-muted-foreground">agent-driver demo</p>
        </div>
      </div>
      <div className="flex min-w-0 items-center gap-2 overflow-x-auto pb-1 sm:justify-end sm:overflow-visible sm:pb-0">
        {lastAssistantMetadata?.promptTokens != null ||
        lastAssistantMetadata?.completionTokens != null ? (
          <Badge variant="outline" className="shrink-0 gap-1.5 text-xs font-mono">
            <span className="text-muted-foreground">↑ prompt</span>
            {lastAssistantMetadata.promptTokens ?? 0}
            <span className="text-muted-foreground">· ↓ completion</span>
            {lastAssistantMetadata.completionTokens ?? 0}
          </Badge>
        ) : null}
        {runId ? (
          <Badge variant="outline" className="shrink-0 gap-1.5 text-xs font-mono">
            <Radio className="h-3.5 w-3.5 text-muted-foreground" />
            run {Math.max(runCount, 1)}
            <span className="text-muted-foreground">{shortRunId(runId)}</span>
          </Badge>
        ) : null}
        <Tooltip>
          <TooltipTrigger asChild>
            <Badge
              variant="secondary"
              role="status"
              aria-label={`Provider status: ${providerLabel}`}
              className="shrink-0 gap-1.5 text-xs"
            >
              <Circle className={`h-2 w-2 ${providerTone}`} />
              {providerLabel}
            </Badge>
          </TooltipTrigger>
          <TooltipContent side="bottom" className="max-w-80">
            {providerTooltip}
          </TooltipContent>
        </Tooltip>
        <ModelPicker />
        <button
          type="button"
          className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
          onClick={toggleTheme}
          aria-label="Toggle theme"
        >
          {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </button>
      </div>
    </div>
  );
}
