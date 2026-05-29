import { Check, Copy, RotateCcw, Trash2 } from "lucide-react";
import { useState } from "react";

import { cn } from "../../lib/cn";
import type { AssistantMessageMetadata } from "../../lib/messageMetadata";
import { Button } from "../ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "../ui/tooltip";
import { MessageMetadataPopover } from "./MessageMetadataPopover";

interface MessageActionsProps {
  content: string;
  metadata?: AssistantMessageMetadata;
  showRetry?: boolean;
  showMetadata?: boolean;
  disabled?: boolean;
  align?: "start" | "end";
  onRetry?: () => void;
  onDelete?: () => void;
}

function ActionButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string;
  disabled?: boolean;
  onClick?: () => void;
  children: React.ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          className="h-8 w-8 text-muted-foreground hover:text-foreground"
          aria-label={label}
          disabled={disabled}
          onClick={onClick}
        >
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent side="top">{label}</TooltipContent>
    </Tooltip>
  );
}

export function MessageActions({
  content,
  metadata,
  showRetry = false,
  showMetadata = false,
  disabled,
  align = "start",
  onRetry,
  onDelete,
}: MessageActionsProps) {
  const [copied, setCopied] = useState(false);

  const copyContent = async () => {
    if (!content.trim()) {
      return;
    }
    await navigator.clipboard.writeText(content);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div
      className={cn(
        "flex items-center gap-0.5 transition-opacity duration-150",
        "opacity-100 sm:opacity-0 sm:pointer-events-none",
        "sm:group-hover:pointer-events-auto sm:group-hover:opacity-100",
        "sm:group-focus-within:pointer-events-auto sm:group-focus-within:opacity-100",
        "sm:focus-within:pointer-events-auto sm:focus-within:opacity-100",
        align === "end" && "justify-end",
      )}
    >
      {showRetry ? (
        <ActionButton label="Retry" disabled={disabled} onClick={onRetry}>
          <RotateCcw className="h-4 w-4" />
        </ActionButton>
      ) : null}
      <ActionButton label="Copy" disabled={disabled || !content.trim()} onClick={() => void copyContent()}>
        {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
      </ActionButton>
      {showMetadata ? (
        <MessageMetadataPopover metadata={metadata} disabled={disabled} align={align} />
      ) : null}
      <ActionButton label="Delete" disabled={disabled} onClick={onDelete}>
        <Trash2 className="h-4 w-4" />
      </ActionButton>
    </div>
  );
}
