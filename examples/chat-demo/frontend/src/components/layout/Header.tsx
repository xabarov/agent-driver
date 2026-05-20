import { useQuery } from "@tanstack/react-query";
import { Circle } from "lucide-react";

import { fetchHealth } from "../../lib/api";
import { Badge } from "../ui/badge";
import { Card } from "../ui/card";

export function Header() {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 5000,
  });

  const providerName = health.data?.provider.provider_name ?? "unknown";
  const isHealthy = health.data?.provider.healthy ?? false;

  return (
    <Card className="flex items-center justify-between px-4 py-3">
      <div>
        <h1 className="text-lg font-semibold">Chat Demo</h1>
        <p className="text-sm text-muted-foreground">Frontend MVP (Stage 2)</p>
      </div>
      <Badge variant="secondary" className="gap-2">
        <Circle
          className={`h-2.5 w-2.5 ${
            isHealthy ? "fill-emerald-500 text-emerald-500" : "fill-red-500 text-red-500"
          }`}
        />
        provider: {providerName}
      </Badge>
    </Card>
  );
}
