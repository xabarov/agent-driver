import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, ChevronDown, Search } from "lucide-react";

import { fetchModels, fetchProviders } from "../../lib/api";
import { useSettingsStore } from "../../store/settingsStore";
import type { ModelView } from "../../types/api";
import { Button } from "../ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../ui/dropdown-menu";

const RECENT_MODELS_KEY = "chat-demo-recent-models";
const MAX_RECENT_MODELS = 5;
const MAX_VISIBLE_MODELS = 90;

function readRecentModels(): string[] {
  try {
    const raw = localStorage.getItem(RECENT_MODELS_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string") : [];
  } catch {
    return [];
  }
}

function writeRecentModel(id: string) {
  const recent = readRecentModels().filter((item) => item !== id);
  localStorage.setItem(RECENT_MODELS_KEY, JSON.stringify([id, ...recent].slice(0, MAX_RECENT_MODELS)));
}

function providerFromModelId(id: string): string {
  return id.includes("/") ? id.split("/")[0] || "model" : "model";
}

function modelLabel(entry: ModelView): string {
  return entry.name && entry.name !== entry.id ? entry.name : entry.id;
}

function ModelRow({
  entry,
  selected,
  onSelect,
}: {
  entry: ModelView;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  return (
    <DropdownMenuItem
      onClick={() => onSelect(entry.id)}
      className="grid min-h-12 grid-cols-[1fr_auto] gap-3 rounded-md px-2.5 py-2"
    >
      <span className="min-w-0">
        <span className="block truncate text-sm font-medium">{modelLabel(entry)}</span>
        <span className="mt-0.5 flex min-w-0 items-center gap-2">
          <span className="rounded border border-border bg-background px-1.5 py-0.5 text-[10px] font-medium uppercase text-muted-foreground">
            {providerFromModelId(entry.id)}
          </span>
          <span className="truncate font-mono text-[11px] text-muted-foreground">{entry.id}</span>
        </span>
      </span>
      {selected ? <Check className="mt-1 h-4 w-4 text-primary" aria-hidden /> : null}
    </DropdownMenuItem>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="py-1">
      <DropdownMenuLabel className="sticky top-0 z-10 bg-popover/95 text-[11px] uppercase backdrop-blur">
        {title}
      </DropdownMenuLabel>
      <div className="px-1">{children}</div>
    </div>
  );
}

export function ModelPicker() {
  const model = useSettingsStore((state) => state.model);
  const setModel = useSettingsStore((state) => state.setModel);
  const [search, setSearch] = useState("");
  const [recentIds, setRecentIds] = useState<string[]>(() => readRecentModels());

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

  const models = modelsQuery.data?.models ?? [];
  const modelById = useMemo(() => new Map(models.map((entry) => [entry.id, entry])), [models]);
  const selectedId = model || defaultModel;
  const selectedEntry =
    (selectedId ? modelById.get(selectedId) : undefined) ??
    (selectedId
      ? { id: selectedId, name: null, description: null, context_length: null }
      : undefined);
  const recentModels = recentIds
    .filter((id) => id !== selectedId)
    .map((id) => modelById.get(id))
    .filter((entry): entry is ModelView => Boolean(entry));

  const filtered = useMemo(() => {
    const list = modelsQuery.data?.models ?? [];
    const needle = search.trim().toLowerCase();
    if (!needle) {
      return list.slice(0, MAX_VISIBLE_MODELS);
    }
    return list
      .filter(
        (entry) =>
          entry.id.toLowerCase().includes(needle) ||
          (entry.name ?? "").toLowerCase().includes(needle),
      )
      .slice(0, MAX_VISIBLE_MODELS);
  }, [modelsQuery.data?.models, search]);

  const allModels = filtered.filter(
    (entry) => entry.id !== selectedId && !recentModels.some((recent) => recent.id === entry.id),
  );
  const label = model || defaultModel || "Select model";
  const selectModel = (id: string) => {
    setModel(id);
    writeRecentModel(id);
    setRecentIds(readRecentModels());
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          className="max-w-[14rem] shrink-0 gap-1 font-normal sm:max-w-[18rem]"
        >
          <span className="truncate">{label}</span>
          <ChevronDown className="h-3.5 w-3.5 shrink-0 opacity-60" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        className="w-[min(34rem,calc(100vw-1rem))] overflow-hidden p-0"
      >
        <div className="border-b border-border bg-popover p-2">
          <div className="mb-2 flex items-center justify-between gap-3 px-1">
            <div>
              <p className="text-sm font-medium">Model</p>
              <p className="text-xs text-muted-foreground">
                {providers.data?.name ?? modelsQuery.data?.provider ?? "provider"}
              </p>
            </div>
            {selectedId ? (
              <span className="max-w-[12rem] truncate rounded-full border border-border bg-background px-2 py-1 font-mono text-[11px] text-muted-foreground">
                {providerFromModelId(selectedId)}
              </span>
            ) : null}
          </div>
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
        <div className="max-h-[min(28rem,70vh)] overflow-y-auto p-1">
          {modelsQuery.isLoading ? (
            <DropdownMenuItem disabled>Loading models...</DropdownMenuItem>
          ) : null}
          {modelsQuery.isError ? (
            <DropdownMenuItem disabled>Failed to load models</DropdownMenuItem>
          ) : null}
          {selectedEntry ? (
            <Section title={model ? "Selected" : "Default"}>
              <ModelRow entry={selectedEntry} selected onSelect={selectModel} />
            </Section>
          ) : null}
          {!search.trim() && recentModels.length > 0 ? (
            <>
              <DropdownMenuSeparator />
              <Section title="Recent">
                {recentModels.map((entry) => (
                  <ModelRow
                    key={entry.id}
                    entry={entry}
                    selected={entry.id === selectedId}
                    onSelect={selectModel}
                  />
                ))}
              </Section>
            </>
          ) : null}
          <DropdownMenuSeparator />
          <Section title={search.trim() ? "Matching Models" : "All Models"}>
            {allModels.map((entry) => (
              <ModelRow
                key={entry.id}
                entry={entry}
                selected={entry.id === selectedId}
                onSelect={selectModel}
              />
            ))}
            {!modelsQuery.isLoading && !modelsQuery.isError && allModels.length === 0 ? (
              <DropdownMenuItem disabled>No matching models</DropdownMenuItem>
            ) : null}
          </Section>
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
