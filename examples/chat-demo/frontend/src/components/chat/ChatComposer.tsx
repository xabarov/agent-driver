import { useState } from "react";
import { createPortal } from "react-dom";
import {
  ArrowUp,
  BookOpenCheck,
  ChevronDown,
  Library,
  MessageSquare,
  Search,
  ShieldCheck,
  Square,
  Wrench,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { cn } from "../../lib/cn";
import type { SteeringControl } from "../../store/chatStore";
import { ToolsPicker } from "../settings/ToolsPicker";
import { SkillsPanel } from "../settings/SkillsPanel";
import {
  normalizeToolPreset,
  toolPresetLabel,
  type ResearchMode,
  type ResearchProfile,
  useSettingsStore,
} from "../../store/settingsStore";
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
  const [modeOpen, setModeOpen] = useState(false);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [skillsOpen, setSkillsOpen] = useState(false);
  const toolPreset = normalizeToolPreset(useSettingsStore((state) => state.toolPreset));
  const researchMode = useSettingsStore((state) => state.researchMode);
  const researchProfile = useSettingsStore((state) => state.researchProfile);
  const setResearchMode = useSettingsStore((state) => state.setResearchMode);
  const setResearchProfile = useSettingsStore((state) => state.setResearchProfile);
  const canSteer = streaming && !disabled && Boolean(onSteer);
  const modeLabel =
    researchMode === "deep"
      ? `Deep: ${researchProfile === "hard" ? "Hard" : "Medium"}`
      : researchMode === "chat"
        ? "Chat"
        : "Web";

  const selectMode = (mode: ResearchMode) => {
    setResearchMode(mode);
    if (mode === "deep" && researchProfile === "light") {
      setResearchProfile("medium");
    }
    if (mode !== "deep") {
      setModeOpen(false);
    }
  };

  const selectProfile = (profile: ResearchProfile) => {
    setResearchMode("deep");
    setResearchProfile(profile);
  };

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
      {modeOpen || toolsOpen || skillsOpen
        ? createPortal(
            <button
              type="button"
              aria-label="Close popover"
              className="fixed inset-0 z-[95] cursor-default bg-black/55 backdrop-blur-[1px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              onClick={() => {
                setModeOpen(false);
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
            <Popover open={modeOpen} onOpenChange={setModeOpen}>
              <PopoverTrigger asChild>
                <Button
                  type="button"
                  size="sm"
                  variant={researchMode === "deep" ? "default" : "secondary"}
                  className={cn(
                    "gap-1.5",
                    researchMode === "deep" &&
                      "bg-emerald-600 text-white hover:bg-emerald-700",
                  )}
                  disabled={disabled || streaming}
                  aria-label={`Research mode: ${modeLabel}`}
                >
                  {researchMode === "chat" ? (
                    <MessageSquare className="h-4 w-4" />
                  ) : researchMode === "web" ? (
                    <Search className="h-4 w-4" />
                  ) : (
                    <BookOpenCheck className="h-4 w-4" />
                  )}
                  <span className="hidden sm:inline">{modeLabel}</span>
                  <ChevronDown className="h-3.5 w-3.5 opacity-70" />
                </Button>
              </PopoverTrigger>
              <PopoverContent
                side="top"
                align="start"
                sideOffset={8}
                collisionPadding={16}
                className="z-[110] w-[min(25rem,calc(100vw-2rem))] border-border/80 p-3"
              >
                <div className="space-y-3">
                  <div className="grid grid-cols-3 gap-1">
                    <ModeButton
                      active={researchMode === "chat"}
                      icon={MessageSquare}
                      label="Chat"
                      description="No research"
                      onClick={() => selectMode("chat")}
                    />
                    <ModeButton
                      active={researchMode === "web"}
                      icon={Search}
                      label="Web"
                      description="Fast links"
                      onClick={() => selectMode("web")}
                    />
                    <ModeButton
                      active={researchMode === "deep"}
                      icon={BookOpenCheck}
                      label="Deep"
                      description="Report"
                      onClick={() => selectMode("deep")}
                    />
                  </div>

                  {researchMode === "deep" ? (
                    <div className="space-y-2 rounded-md border border-border/70 bg-background/60 p-2">
                      <ProfileButton
                        active={researchProfile !== "hard"}
                        label="Medium"
                        badge="Recommended"
                        description="Creates research/report.md with sources and bounded subagents."
                        onClick={() => selectProfile("medium")}
                      />
                      <ProfileButton
                        active={researchProfile === "hard"}
                        label="Hard"
                        badge="Opt-in"
                        description="Higher token budget for audited sources and hard fallbacks."
                        icon={ShieldCheck}
                        onClick={() => selectProfile("hard")}
                      />
                    </div>
                  ) : (
                    <p className="text-xs leading-5 text-muted-foreground">
                      Web is for short source-backed answers. Deep creates a
                      durable report artifact and keeps the final chat concise.
                    </p>
                  )}
                </div>
              </PopoverContent>
            </Popover>
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

interface ModeButtonProps {
  active: boolean;
  icon: LucideIcon;
  label: string;
  description: string;
  onClick: () => void;
}

function ModeButton({
  active,
  icon: Icon,
  label,
  description,
  onClick,
}: ModeButtonProps) {
  return (
    <button
      type="button"
      className={cn(
        "flex min-h-16 flex-col items-center justify-center gap-1 rounded-md border px-2 py-2 text-center text-xs transition-colors",
        active
          ? "border-primary/40 bg-primary/10 text-primary"
          : "border-border/70 bg-background/60 text-muted-foreground hover:text-foreground",
      )}
      onClick={onClick}
    >
      <Icon className="h-4 w-4" />
      <span className="font-medium">{label}</span>
      <span className="text-[11px] leading-4 opacity-80">{description}</span>
    </button>
  );
}

interface ProfileButtonProps {
  active: boolean;
  label: string;
  badge: string;
  description: string;
  icon?: LucideIcon;
  onClick: () => void;
}

function ProfileButton({
  active,
  label,
  badge,
  description,
  icon: Icon = BookOpenCheck,
  onClick,
}: ProfileButtonProps) {
  return (
    <button
      type="button"
      className={cn(
        "flex w-full items-start gap-2 rounded-md border px-2.5 py-2 text-left text-xs transition-colors",
        active
          ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
          : "border-border/70 bg-card/60 text-muted-foreground hover:text-foreground",
      )}
      onClick={onClick}
    >
      <Icon className="mt-0.5 h-4 w-4 shrink-0" />
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5">
          <span className="font-medium">{label}</span>
          <span className="rounded-full border border-current/25 px-1.5 py-0.5 text-[10px]">
            {badge}
          </span>
        </span>
        <span className="mt-1 block leading-5">{description}</span>
      </span>
    </button>
  );
}
