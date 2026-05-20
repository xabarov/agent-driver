import { cn } from "../../lib/cn";
import type { ToolPreset } from "../../store/settingsStore";
import { useSettingsStore } from "../../store/settingsStore";

const PRESETS: ToolPreset[] = ["off", "safe", "dev", "all"];

interface ToolsPickerProps {
  disabled?: boolean;
}

export function ToolsPicker({ disabled }: ToolsPickerProps) {
  const toolPreset = useSettingsStore((state) => state.toolPreset);
  const setToolPreset = useSettingsStore((state) => state.setToolPreset);

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-xs text-muted-foreground">Tools</span>
      {PRESETS.map((preset) => (
        <button
          key={preset}
          type="button"
          disabled={disabled}
          onClick={() => setToolPreset(preset)}
          className={cn(
            "rounded-full border px-2.5 py-1 text-xs capitalize transition-colors",
            toolPreset === preset
              ? "border-primary bg-primary text-primary-foreground"
              : "border-border bg-background hover:bg-secondary",
            disabled && "pointer-events-none opacity-50",
          )}
        >
          {preset}
        </button>
      ))}
    </div>
  );
}
