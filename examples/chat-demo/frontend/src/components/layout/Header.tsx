import { useQuery } from "@tanstack/react-query";
import { Circle, Moon, Sun } from "lucide-react";

import { fetchHealth, fetchProviders } from "../../lib/api";
import { useChatStore } from "../../store/chatStore";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Card } from "../ui/card";
import { useThemeMode } from "./ThemeProvider";

export function Header() {
  const { theme, toggleTheme } = useThemeMode();
  const tokenUsage = useChatStore((state) => state.tokenUsage);
  const health = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 5000,
  });
  const providers = useQuery({
    queryKey: ["providers"],
    queryFn: fetchProviders,
    refetchInterval: 10000,
  });

  const providerName = health.data?.provider.provider_name ?? "unknown";
  const isHealthy = health.data?.provider.healthy ?? false;
  const model = providers.data?.model ?? "default";

  return (
    <Card className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
      <div>
        <h1 className="text-lg font-semibold">Chat Demo</h1>
        <p className="text-sm text-muted-foreground">Stages 4–7 · tools, HITL, replay</p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {tokenUsage?.prompt != null || tokenUsage?.completion != null ? (
          <Badge variant="outline" className="text-xs">
            ↑ {tokenUsage.prompt ?? 0} ↓ {tokenUsage.completion ?? 0}
          </Badge>
        ) : null}
        <Badge variant="secondary" className="gap-2">
          <Circle
            className={`h-2.5 w-2.5 ${
              isHealthy ? "fill-emerald-500 text-emerald-500" : "fill-red-500 text-red-500"
            }`}
          />
          {providerName} · {model}
        </Badge>
        <Button type="button" size="icon" variant="ghost" onClick={toggleTheme}>
          {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </Button>
      </div>
    </Card>
  );
}
