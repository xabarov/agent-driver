import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Wrench } from "lucide-react";

import { cn } from "../../lib/cn";
import { importSampleWorkspace } from "../../lib/api";
import { PRESET_HINTS, toolsQueryKey, useToolsForPreset } from "../../lib/tools";
import { useChatStore } from "../../store/chatStore";
import type { ToolPreset } from "../../store/settingsStore";
import { useSettingsStore } from "../../store/settingsStore";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { ToggleGroup, ToggleGroupItem } from "../ui/toggle-group";

const PRESETS: ToolPreset[] = ["off", "safe", "workspace", "dev", "all"];
const VISIBLE_TOOL_LIMIT = 8;
const WORKSPACE_PRESETS = new Set<ToolPreset>(["workspace", "dev", "all"]);

interface ToolsPickerProps {
  disabled?: boolean;
  compact?: boolean;
  onPresetChange?: () => void;
}

export function ToolsPicker({ disabled, compact, onPresetChange }: ToolsPickerProps) {
  const queryClient = useQueryClient();
  const sessionId = useChatStore((state) => state.sessionId);
  const toolPreset = useSettingsStore((state) => state.toolPreset);
  const setToolPreset = useSettingsStore((state) => state.setToolPreset);
  const toolsQuery = useToolsForPreset(toolPreset, sessionId);
  const [showAllTools, setShowAllTools] = useState(false);
  const sampleMutation = useMutation({
    mutationFn: () => importSampleWorkspace(sessionId ?? ""),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: toolsQueryKey(toolPreset, sessionId) });
    },
  });

  const toolNames = toolsQuery.data?.tools.map((tool) => tool.name) ?? [];
  const visibleTools = showAllTools ? toolNames : toolNames.slice(0, VISIBLE_TOOL_LIMIT);
  const hiddenCount = Math.max(0, toolNames.length - VISIBLE_TOOL_LIMIT);
  const workspace = toolsQuery.data?.workspace;
  const showWorkspaceDetails = WORKSPACE_PRESETS.has(toolPreset);

  return (
    <div className={cn("space-y-2", compact && "space-y-1.5")}>
      <div className="flex flex-wrap items-center gap-2">
        {!compact ? (
          <span className="inline-flex items-center gap-1 text-xs font-medium text-muted-foreground">
            <Wrench className="h-3.5 w-3.5" />
            Tools
          </span>
        ) : null}
        <ToggleGroup
          type="single"
          value={toolPreset}
          onValueChange={(value) => {
            if (value && PRESETS.includes(value as ToolPreset)) {
              setToolPreset(value as ToolPreset);
              setShowAllTools(false);
              onPresetChange?.();
            }
          }}
          disabled={disabled}
          aria-label="Tool preset"
        >
          {PRESETS.map((preset) => (
            <ToggleGroupItem key={preset} value={preset} aria-label={PRESET_HINTS[preset]}>
              {preset}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </div>
      <p className="text-xs text-muted-foreground">{PRESET_HINTS[toolPreset]}</p>
      {showWorkspaceDetails ? (
        <div className="rounded-lg border border-border/70 bg-background/60 p-2 text-xs">
          <div className="font-medium text-foreground">Session workspace</div>
          {sessionId ? (
            <>
              <div className="mt-1 text-muted-foreground">
                {workspace?.exists ? `${workspace.fileCount} files available` : "Empty workspace"}
              </div>
              {workspace?.root ? (
                <div className="mt-1 truncate font-mono text-muted-foreground" title={workspace.root}>
                  {workspace.root}
                </div>
              ) : null}
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="mt-2 h-7 px-2 text-xs"
                disabled={disabled || sampleMutation.isPending}
                onClick={() => sampleMutation.mutate()}
              >
                {sampleMutation.isPending ? "Importing…" : "Import sample project"}
              </Button>
              {sampleMutation.isSuccess ? (
                <span className="ml-2 text-muted-foreground">Sample files ready.</span>
              ) : null}
            </>
          ) : (
            <div className="mt-1 text-muted-foreground">
              Workspace is created after the first message in a session.
            </div>
          )}
        </div>
      ) : null}
      {toolPreset === "all" ? (
        <p className="flex items-center gap-1 text-xs text-amber-400">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          Full tool surface — use with care.
        </p>
      ) : null}
      {!toolsQuery.isLoading && !toolsQuery.isError ? (
        <p className="text-xs font-medium text-foreground">
          {toolNames.length === 0 ? "No tools enabled" : `${toolNames.length} tools enabled`}
        </p>
      ) : null}
      <div
        className="tools-picker-scroll flex max-h-[min(50vh,12rem)] min-h-6 flex-wrap gap-1 overflow-y-auto pr-1"
        data-testid="tools-picker-scroll"
      >
        {toolsQuery.isLoading ? (
          <span className="text-xs text-muted-foreground">Loading tools…</span>
        ) : null}
        {toolsQuery.isError ? (
          <span className="text-xs text-destructive">Failed to load tools list.</span>
        ) : null}
        {!toolsQuery.isLoading && !toolsQuery.isError && toolNames.length === 0 ? (
          <Badge variant="outline" className="text-xs">
            No tools
          </Badge>
        ) : null}
        {visibleTools.map((name) => (
          <Badge key={name} variant="secondary" className="font-mono text-xs">
            {name}
          </Badge>
        ))}
      </div>
      {hiddenCount > 0 && !showAllTools ? (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-7 px-2 text-xs"
          onClick={() => setShowAllTools(true)}
        >
          +{hiddenCount} more
        </Button>
      ) : null}
      {showAllTools && toolNames.length > VISIBLE_TOOL_LIMIT ? (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-7 px-2 text-xs"
          onClick={() => setShowAllTools(false)}
        >
          Show less
        </Button>
      ) : null}
    </div>
  );
}
