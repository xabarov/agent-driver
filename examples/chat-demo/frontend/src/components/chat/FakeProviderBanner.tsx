import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";

import { fetchProviders } from "../../lib/api";
import { useSettingsStore } from "../../store/settingsStore";

export function FakeProviderBanner() {
  const toolPreset = useSettingsStore((state) => state.toolPreset);
  const providers = useQuery({
    queryKey: ["providers"],
    queryFn: fetchProviders,
    staleTime: 30_000,
  });

  const name = providers.data?.name ?? "";
  if (name !== "fake") {
    return null;
  }

  return (
    <div className="mb-3 flex gap-2 rounded-lg border border-amber-500/50 bg-amber-500/10 px-3 py-2 text-sm text-amber-100">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
      <div>
        <p className="font-medium">Demo mode: Fake provider</p>
        <p className="text-amber-100/80">
          Every reply is fixed to &quot;ok&quot; — no real LLM and no web search. Set{" "}
          <code className="rounded bg-black/30 px-1">AGENT_DRIVER_PROVIDER=openrouter</code>{" "}
          (and API key) in the repo <code className="rounded bg-black/30 px-1">.env</code>, restart{" "}
          <code className="rounded bg-black/30 px-1">make dev-full</code>, then choose Tools{" "}
          <strong>Safe</strong> or <strong>All</strong>
          {toolPreset === "off" ? " (currently Off — tools disabled)." : "."}
        </p>
      </div>
    </div>
  );
}
