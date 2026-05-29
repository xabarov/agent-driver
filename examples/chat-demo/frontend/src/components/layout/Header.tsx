import { useQuery } from "@tanstack/react-query";
import { Circle, MessageSquare, Moon, Sun } from "lucide-react";

import { fetchHealth } from "../../lib/api";
import { useChatStore } from "../../store/chatStore";
import { ModelPicker } from "./ModelPicker";
import { Badge } from "../ui/badge";
import { useThemeMode } from "./ThemeProvider";

export function Header() {
  const { theme, toggleTheme } = useThemeMode();
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

  const providerName = health.data?.provider.provider_name ?? "unknown";
  const isHealthy = health.data?.provider.healthy ?? false;

  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex items-center gap-2">
        <MessageSquare className="h-5 w-5 text-primary" />
        <div>
          <h1 className="text-base font-semibold leading-tight">Chat</h1>
          <p className="text-xs text-muted-foreground">agent-driver demo</p>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {lastAssistantMetadata?.promptTokens != null ||
        lastAssistantMetadata?.completionTokens != null ? (
          <Badge variant="outline" className="gap-1.5 text-xs font-mono">
            <span className="text-muted-foreground">↑ prompt</span>
            {lastAssistantMetadata.promptTokens ?? 0}
            <span className="text-muted-foreground">· ↓ completion</span>
            {lastAssistantMetadata.completionTokens ?? 0}
          </Badge>
        ) : null}
        <Badge variant="secondary" className="gap-1.5 text-xs">
          <Circle
            className={`h-2 w-2 ${
              isHealthy ? "fill-emerald-500 text-emerald-500" : "fill-red-500 text-red-500"
            }`}
          />
          {providerName}
        </Badge>
        <ModelPicker />
        <button
          type="button"
          className="inline-flex h-9 w-9 items-center justify-center rounded-md hover:bg-secondary"
          onClick={toggleTheme}
          aria-label="Toggle theme"
        >
          {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </button>
      </div>
    </div>
  );
}
