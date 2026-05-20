import { BarChart3 } from "lucide-react";

import { cn } from "../../lib/cn";
import {
  formatCostUsd,
  formatDurationSec,
  formatTokenCount,
  formatTokensPerSecond,
  hasMetadataContent,
  type AssistantMessageMetadata,
} from "../../lib/messageMetadata";
import { Button } from "../ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "../ui/popover";

interface MessageMetadataPopoverProps {
  metadata?: AssistantMessageMetadata;
  disabled?: boolean;
  align?: "start" | "end";
}

export function MessageMetadataPopover({
  metadata,
  disabled,
  align = "start",
}: MessageMetadataPopoverProps) {
  const hasContent = hasMetadataContent(metadata);
  const provider = metadata?.provider?.toLowerCase() ?? "";
  const showActivityLink = provider.includes("openrouter");

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          className={cn(
            "h-8 w-8 text-muted-foreground hover:text-foreground",
            "data-[state=open]:bg-secondary data-[state=open]:text-foreground",
          )}
          aria-label="Metadata"
          disabled={disabled}
        >
          <BarChart3 className="h-4 w-4" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        side="top"
        align={align === "end" ? "end" : "start"}
        className="w-72 p-3 text-xs"
      >
        {hasContent && metadata ? (
          <dl className="space-y-1.5">
            <div className="flex justify-between gap-4">
              <dt className="text-muted-foreground">Tokens per second</dt>
              <dd className="font-medium tabular-nums">
                {formatTokensPerSecond(metadata.tokensPerSecond)}
              </dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-muted-foreground">Token count</dt>
              <dd className="font-medium tabular-nums">{formatTokenCount(metadata)}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-muted-foreground">Cost</dt>
              <dd className="font-medium tabular-nums">{formatCostUsd(metadata.costUsd)}</dd>
            </div>
            <div className="flex justify-between gap-4 border-t border-border pt-1.5">
              <dt className="text-muted-foreground">Duration</dt>
              <dd className="font-medium tabular-nums">
                {formatDurationSec(metadata.durationMs)}
              </dd>
            </div>
          </dl>
        ) : (
          <p className="text-muted-foreground">No usage data for this message.</p>
        )}
        <p className="mt-2 border-t border-border pt-2 text-[11px] leading-snug text-muted-foreground">
          Token count is estimated. To see the model&apos;s native token count and how much you were
          charged, visit the{" "}
          {showActivityLink ? (
            <a
              href="https://openrouter.ai/activity"
              target="_blank"
              rel="noreferrer"
              className="text-primary underline-offset-2 hover:underline"
            >
              activity
            </a>
          ) : (
            "activity"
          )}{" "}
          page.
        </p>
      </PopoverContent>
    </Popover>
  );
}
