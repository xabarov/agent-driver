import { Globe, Link2, Search } from "lucide-react";

import { PRESET_HINTS } from "../../lib/tools";
import {
  normalizeToolPreset,
  toolPresetLabel,
  type ToolPreset,
} from "../../store/settingsStore";
import { useSettingsStore } from "../../store/settingsStore";

interface ToolsPickerProps {
  disabled?: boolean;
  compact?: boolean;
  onPresetChange?: () => void;
}

function presetFromToggles(webSearch: boolean, webFetch: boolean): ToolPreset {
  if (webSearch && webFetch) {
    return "web";
  }
  if (webSearch) {
    return "web_search";
  }
  if (webFetch) {
    return "web_fetch";
  }
  return "off";
}

interface ToolToggleProps {
  checked: boolean;
  disabled?: boolean;
  icon: typeof Search;
  title: string;
  description: string;
  onChange: (checked: boolean) => void;
}

function ToolToggle({
  checked,
  disabled,
  icon: Icon,
  title,
  description,
  onChange,
}: ToolToggleProps) {
  return (
    <label className="flex items-center gap-3 rounded-lg border border-border/70 bg-background/60 px-3 py-2.5 text-sm">
      <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
      <span className="min-w-0 flex-1">
        <span className="block font-medium text-foreground">{title}</span>
        <span className="block text-xs leading-5 text-muted-foreground">{description}</span>
      </span>
      <input
        type="checkbox"
        className="h-4 w-4 shrink-0 accent-primary"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
    </label>
  );
}

export function ToolsPicker({ disabled, compact, onPresetChange }: ToolsPickerProps) {
  const toolPreset = normalizeToolPreset(useSettingsStore((state) => state.toolPreset));
  const setToolPreset = useSettingsStore((state) => state.setToolPreset);
  const webSearch = toolPreset === "web" || toolPreset === "web_search";
  const webFetch = toolPreset === "web" || toolPreset === "web_fetch";

  const update = (next: { webSearch?: boolean; webFetch?: boolean }) => {
    const preset = presetFromToggles(next.webSearch ?? webSearch, next.webFetch ?? webFetch);
    setToolPreset(preset);
    onPresetChange?.();
  };

  return (
    <div className={compact ? "space-y-2" : "space-y-3"}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          {!compact ? (
            <div className="mb-1 inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
              <Globe className="h-3.5 w-3.5" />
              Server tools
            </div>
          ) : null}
          <p className="text-sm font-medium text-foreground">{toolPresetLabel(toolPreset)}</p>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">{PRESET_HINTS[toolPreset]}</p>
        </div>
      </div>
      <div className="space-y-2">
        <ToolToggle
          checked={webSearch}
          disabled={disabled}
          icon={Search}
          title="Web Search"
          description="Search the web for current information."
          onChange={(checked) => update({ webSearch: checked })}
        />
        <ToolToggle
          checked={webFetch}
          disabled={disabled}
          icon={Link2}
          title="Web Fetch"
          description="Retrieve content from URLs."
          onChange={(checked) => update({ webFetch: checked })}
        />
      </div>
      <p className="text-xs leading-5 text-muted-foreground">
        The agent can use planning when a task needs it. Local file and shell tools are not exposed
        in this web demo.
      </p>
    </div>
  );
}
