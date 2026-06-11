import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Brain, Check, ChevronDown, Search, Wrench } from "lucide-react";

import { controlRun, fetchModels, fetchProviders } from "../../lib/api";
import { useChatStore } from "../../store/chatStore";
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
const POPULAR_MODEL_HINTS = [
  "openai/gpt-5.5",
  "openai/gpt-5",
  "anthropic/claude",
  "google/gemini",
  "qwen/qwen3",
  "deepseek/deepseek-r1",
  "meta-llama/llama",
  "x-ai/grok",
];

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

function modelSupports(entry: ModelView, key: string): boolean | undefined {
  const value = entry.capability_profile?.[key];
  return typeof value === "boolean" ? value : undefined;
}

function CapabilityBadges({ entry }: { entry: ModelView }) {
  const supportsTools = modelSupports(entry, "supports_tool_calls");
  const supportsReasoning = modelSupports(entry, "supports_reasoning");
  return (
    <span className="mt-1 flex min-w-0 flex-wrap gap-1">
      {supportsTools === false ? (
        <span className="inline-flex items-center gap-1 rounded border border-amber-500/30 bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-200">
          <AlertTriangle className="h-3 w-3" aria-hidden />
          tools uncertain
        </span>
      ) : (
        <span className="inline-flex items-center gap-1 rounded border border-emerald-500/20 bg-emerald-500/10 px-1.5 py-0.5 text-[10px] text-emerald-200">
          <Wrench className="h-3 w-3" aria-hidden />
          tools
        </span>
      )}
      {supportsReasoning ? (
        <span className="inline-flex items-center gap-1 rounded border border-sky-500/20 bg-sky-500/10 px-1.5 py-0.5 text-[10px] text-sky-200">
          <Brain className="h-3 w-3" aria-hidden />
          reasoning
        </span>
      ) : null}
    </span>
  );
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
        <CapabilityBadges entry={entry} />
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
  const runId = useChatStore((state) => state.runId);
  const streaming = useChatStore((state) => state.streaming);
  const lastError = useChatStore((state) => state.lastError);
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
  const popularModels = useMemo(() => {
    const selected = new Set([selectedId, ...recentModels.map((entry) => entry.id)]);
    const scored = models
      .filter((entry) => !selected.has(entry.id))
      .filter((entry) => {
        const id = entry.id.toLowerCase();
        const name = (entry.name ?? "").toLowerCase();
        return POPULAR_MODEL_HINTS.some((hint) => id.includes(hint) || name.includes(hint));
      });
    return scored.slice(0, 12);
  }, [models, recentModels, selectedId]);

  const filtered = useMemo(() => {
    const list = modelsQuery.data?.models ?? [];
    const needle = search.trim().toLowerCase();
    if (!needle) {
      return list;
    }
    return list.filter(
      (entry) =>
        entry.id.toLowerCase().includes(needle) ||
        (entry.name ?? "").toLowerCase().includes(needle),
    );
  }, [modelsQuery.data?.models, search]);

  const allModels = filtered.filter(
    (entry) =>
      entry.id !== selectedId &&
      !recentModels.some((recent) => recent.id === entry.id) &&
      (Boolean(search.trim()) ||
        !popularModels.some((popular) => popular.id === entry.id)),
  );
  const label = model || defaultModel || "Select model";
  const selectedWarnings = selectedEntry
    ? [
        modelSupports(selectedEntry, "supports_tool_calls") === false
          ? "Tool calls are not confirmed for this model."
          : null,
        modelSupports(selectedEntry, "supports_streaming") === false
          ? "Streaming is not confirmed for this model."
          : null,
        lastError?.includes("402")
          ? "Last request was rejected with HTTP 402. Check credits or choose another model."
          : null,
      ].filter((item): item is string => Boolean(item))
    : [];
  const selectModel = (id: string) => {
    setModel(id);
    writeRecentModel(id);
    setRecentIds(readRecentModels());
    if (!streaming || !runId) {
      return;
    }
    void controlRun(runId, {
      kind: "set_model",
      priority: "next",
      payload: { model: id },
    })
      .then((response) => {
        if (!response.queue_id) {
          return;
        }
        useChatStore.getState().addSteeringControl({
          queueId: response.queue_id,
          message: `switch model: ${id}`,
          status: "queued",
        });
      })
      .catch((error) => {
        useChatStore.getState().setLastError(
          error instanceof Error ? error.message : "Failed to queue model switch.",
        );
      });
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
                {models.length ? ` · ${models.length} models` : ""}
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
          {selectedWarnings.length > 0 ? (
            <div className="mt-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-2.5 py-2 text-xs text-amber-100">
              {selectedWarnings[0]}
            </div>
          ) : null}
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
          {!search.trim() && popularModels.length > 0 ? (
            <>
              <DropdownMenuSeparator />
              <Section title="Popular">
                {popularModels.map((entry) => (
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
