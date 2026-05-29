import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Search } from "lucide-react";

import { fetchModels, fetchProviders } from "../../lib/api";
import { useSettingsStore } from "../../store/settingsStore";
import { Button } from "../ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../ui/dropdown-menu";

export function ModelPicker() {
  const model = useSettingsStore((state) => state.model);
  const setModel = useSettingsStore((state) => state.setModel);
  const [search, setSearch] = useState("");

  const providers = useQuery({
    queryKey: ["providers"],
    queryFn: fetchProviders,
    staleTime: 60_000,
  });
  const modelsQuery = useQuery({
    queryKey: ["models"],
    queryFn: fetchModels,
    staleTime: 300_000,
  });

  const defaultModel = providers.data?.model ?? "";
  useEffect(() => {
    if (!model && defaultModel) {
      setModel(defaultModel);
    }
  }, [defaultModel, model, setModel]);

  const filtered = useMemo(() => {
    const list = modelsQuery.data?.models ?? [];
    const needle = search.trim().toLowerCase();
    if (!needle) {
      return list.slice(0, 80);
    }
    return list
      .filter(
        (entry) =>
          entry.id.toLowerCase().includes(needle) ||
          (entry.name ?? "").toLowerCase().includes(needle),
      )
      .slice(0, 80);
  }, [modelsQuery.data?.models, search]);

  const label = model || defaultModel || "Select model";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button type="button" variant="secondary" size="sm" className="max-w-[16rem] gap-1 font-normal">
          <span className="truncate">{label}</span>
          <ChevronDown className="h-3.5 w-3.5 shrink-0 opacity-60" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80">
        <DropdownMenuLabel>Model</DropdownMenuLabel>
        <div className="px-2 pb-2">
          <div className="flex items-center gap-2 rounded-md border px-2 py-1">
            <Search className="h-3.5 w-3.5 text-muted-foreground" />
            <input
              className="w-full bg-transparent text-sm outline-none"
              placeholder="Search models…"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              onKeyDown={(event) => event.stopPropagation()}
            />
          </div>
        </div>
        <DropdownMenuSeparator />
        {modelsQuery.isLoading ? (
          <DropdownMenuItem disabled>Loading models…</DropdownMenuItem>
        ) : null}
        {modelsQuery.isError ? (
          <DropdownMenuItem disabled>Failed to load models</DropdownMenuItem>
        ) : null}
        {filtered.map((entry) => (
          <DropdownMenuItem
            key={entry.id}
            onClick={() => setModel(entry.id)}
            className={entry.id === model ? "bg-accent" : undefined}
          >
            <span className="truncate font-mono text-xs">{entry.id}</span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
