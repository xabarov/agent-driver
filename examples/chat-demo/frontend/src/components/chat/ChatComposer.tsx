import { useState } from "react";
import { createPortal } from "react-dom";
import { ArrowUp, BookOpenCheck, Library, Square, Wrench, X } from "lucide-react";

import { cn } from "../../lib/cn";
import type { SteeringControl } from "../../store/chatStore";
import { ToolsPicker } from "../settings/ToolsPicker";
import { SkillsPanel } from "../settings/SkillsPanel";
import { normalizeToolPreset, toolPresetLabel, useSettingsStore } from "../../store/settingsStore";
import { Button } from "../ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "../ui/popover";
import { Textarea } from "../ui/textarea";

interface ChatComposerProps {
  streaming: boolean;
  disabled?: boolean;
  steeringControls?: SteeringControl[];
  onSend: (text: string) => void;
  onSteer?: (text: string) => void;
  onCancelSteering?: (queueId: string) => void;
  onStop: () => void;
}

export function ChatComposer({
  streaming,
  disabled,
  steeringControls = [],
  onSend,
  onSteer,
  onCancelSteering,
  onStop,
}: ChatComposerProps) {
  const [value, setValue] = useState("");
  const [toolsOpen, setToolsOpen] = useState(false);
  const [skillsOpen, setSkillsOpen] = useState(false);
  const toolPreset = normalizeToolPreset(useSettingsStore((state) => state.toolPreset));
  const researchDepth = useSettingsStore((state) => state.researchDepth);
  const setResearchDepth = useSettingsStore((state) => state.setResearchDepth);
  const canSteer = streaming && !disabled && Boolean(onSteer);

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed || disabled) {
      return;
    }
    if (streaming) {
      onSteer?.(trimmed);
    } else {
      onSend(trimmed);
    }
    setValue("");
  };

  return (
    <div className="relative z-20 shrink-0 border-t border-border/60 bg-background/95 px-4 py-3 shadow-[0_-10px_30px_rgba(0,0,0,0.16)]">
      {toolsOpen || skillsOpen
        ? createPortal(
            <button
              type="button"
              aria-label="Close popover"
              className="fixed inset-0 z-[95] cursor-default bg-black/55 backdrop-blur-[1px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              onClick={() => {
                setToolsOpen(false);
                setSkillsOpen(false);
              }}
            />,
            document.body,
          )
        : null}
      <div className="relative z-[100] mx-auto w-full max-w-3xl rounded-lg border border-border bg-card/70 p-3 shadow-sm lg:max-w-4xl xl:max-w-5xl">
        {steeringControls.length > 0 ? (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {steeringControls.map((control) => (
              <span
                key={control.queueId}
                className={cn(
                  "inline-flex max-w-full items-center gap-1 rounded-md border px-2 py-1 text-xs",
                  control.status === "queued" && "border-primary/40 bg-primary/10 text-primary",
                  control.status === "dequeued" && "border-border bg-muted text-muted-foreground",
                  control.status === "applied" && "border-emerald-500/30 bg-emerald-500/10 text-emerald-600",
                  control.status === "cancelled" && "border-border bg-muted/60 text-muted-foreground line-through",
                )}
              >
                <span className="truncate">{control.message}</span>
                {control.status === "queued" && onCancelSteering ? (
                  <button
                    type="button"
                    className="rounded-sm p-0.5 text-current opacity-70 hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    onClick={() => onCancelSteering(control.queueId)}
                    aria-label="Cancel steering message"
                  >
                    <X className="h-3 w-3" />
                  </button>
                ) : null}
              </span>
            ))}
          </div>
        ) : null}
        <Textarea
          value={value}
          onChange={(event) => setValue(event.target.value)}
          placeholder={streaming ? "Steer the running assistant…" : "Message the assistant…"}
          disabled={disabled || (streaming && !canSteer)}
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
                  variant={toolPreset === "web" ? "ghost" : "secondary"}
                  className="gap-1.5 capitalize"
                  disabled={disabled}
                >
                  <Wrench className="h-4 w-4" />
                  <span className="hidden sm:inline">Tools ·</span>
                  {toolPresetLabel(toolPreset)}
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
            <Popover open={skillsOpen} onOpenChange={setSkillsOpen}>
              <PopoverTrigger asChild>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="gap-1.5"
                  disabled={disabled}
                >
                  <Library className="h-4 w-4" />
                  <span className="hidden sm:inline">Skills</span>
                </Button>
              </PopoverTrigger>
              <PopoverContent
                side="top"
                align="start"
                sideOffset={8}
                collisionPadding={16}
                className="z-[110] w-[min(28rem,calc(100vw-2rem))] border-border/80 p-3"
              >
                <SkillsPanel />
              </PopoverContent>
            </Popover>
            <Button
              type="button"
              size="sm"
              variant={researchDepth === "deep_parallel_research" ? "default" : "ghost"}
              className={cn(
                "gap-1.5",
                researchDepth === "deep_parallel_research" &&
                  "bg-emerald-600 text-white hover:bg-emerald-700",
              )}
              disabled={disabled || streaming}
              onClick={() =>
                setResearchDepth(
                  researchDepth === "deep_parallel_research"
                    ? "standard"
                    : "deep_parallel_research",
                )
              }
            >
              <BookOpenCheck className="h-4 w-4" />
              <span className="hidden sm:inline">Deep</span>
            </Button>
            <span className="hidden text-xs text-muted-foreground sm:inline">
              ⌘/Ctrl + Enter to send
            </span>
          </div>
          {streaming ? (
            <div className="flex items-center gap-1">
              <Button
                type="button"
                size="icon"
                variant={value.trim() && canSteer ? "default" : "secondary"}
                className={cn(value.trim() && canSteer && "bg-primary text-primary-foreground")}
                onClick={submit}
                disabled={!canSteer || !value.trim()}
                aria-label="Queue steering message"
              >
                <ArrowUp className="h-4 w-4" />
              </Button>
              <Button type="button" size="icon" variant="destructive" onClick={onStop} aria-label="Stop">
                <Square className="h-4 w-4" />
              </Button>
            </div>
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
