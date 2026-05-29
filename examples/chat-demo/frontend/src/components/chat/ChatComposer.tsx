import { useState } from "react";
import { createPortal } from "react-dom";
import { ArrowUp, Square, Wrench } from "lucide-react";

import { cn } from "../../lib/cn";
import { ToolsPicker } from "../settings/ToolsPicker";
import { useSettingsStore } from "../../store/settingsStore";
import { Button } from "../ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "../ui/popover";
import { Textarea } from "../ui/textarea";

interface ChatComposerProps {
  streaming: boolean;
  disabled?: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
}

export function ChatComposer({ streaming, disabled, onSend, onStop }: ChatComposerProps) {
  const [value, setValue] = useState("");
  const [toolsOpen, setToolsOpen] = useState(false);
  const toolPreset = useSettingsStore((state) => state.toolPreset);
  const forcePlanning = useSettingsStore((state) => state.forcePlanning);

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed || streaming || disabled) {
      return;
    }
    onSend(trimmed);
    setValue("");
  };

  return (
    <div className="relative z-20 shrink-0 border-t border-border/60 bg-background/95 px-4 py-3 shadow-[0_-10px_30px_rgba(0,0,0,0.16)]">
      {toolsOpen
        ? createPortal(
            <button
              type="button"
              aria-label="Close tools"
              className="fixed inset-0 z-[95] cursor-default bg-black/55 backdrop-blur-[1px]"
              onClick={() => setToolsOpen(false)}
            />,
            document.body,
          )
        : null}
      <div className="relative z-[100] mx-auto w-full max-w-3xl rounded-lg border border-border bg-card/70 p-3 shadow-sm lg:max-w-4xl xl:max-w-5xl">
        <Textarea
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder="Message the assistant…"
          disabled={streaming || disabled}
          rows={1}
          className="min-h-[2.5rem] max-h-40 resize-none border-0 bg-transparent px-1 shadow-none focus-visible:ring-0"
          style={{ fieldSizing: "content" } as React.CSSProperties}
          onKeyDown={(event) => {
            if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
              event.preventDefault();
              submit();
            }
          }}
        />
        <div className="mt-2 flex items-center justify-between gap-2">
          <div className="flex items-center gap-1">
            <Popover open={toolsOpen} onOpenChange={setToolsOpen}>
              <PopoverTrigger asChild>
                <Button
                  type="button"
                  size="sm"
                  variant={toolPreset === "safe" ? "ghost" : "secondary"}
                  className="gap-1.5 capitalize"
                  disabled={disabled}
                >
                  <Wrench className="h-4 w-4" />
                  <span className="hidden sm:inline">Tools ·</span>
                  {toolPreset}
                  {forcePlanning ? " · plan" : ""}
                </Button>
              </PopoverTrigger>
              <PopoverContent
                side="top"
                align="start"
                sideOffset={8}
                collisionPadding={16}
                className="z-[110] w-[min(24rem,calc(100vw-2rem))] border-border/80 p-3"
              >
                <ToolsPicker
                  disabled={streaming || disabled}
                  compact
                  onPresetChange={() => setToolsOpen(false)}
                />
              </PopoverContent>
            </Popover>
            <span className="hidden text-xs text-muted-foreground sm:inline">
              ⌘/Ctrl + Enter to send
            </span>
          </div>
          {streaming ? (
            <Button type="button" size="icon" variant="destructive" onClick={onStop} aria-label="Stop">
              <Square className="h-4 w-4" />
            </Button>
          ) : (
            <Button
              type="button"
              size="icon"
              variant={value.trim() ? "default" : "secondary"}
              className={cn(
                "transition-transform",
                value.trim() && "bg-primary text-primary-foreground hover:scale-[1.03]",
              )}
              onClick={submit}
              disabled={disabled || !value.trim()}
              aria-label="Send"
            >
              <ArrowUp className="h-4 w-4" />
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
