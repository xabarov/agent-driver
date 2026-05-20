import { cn } from "../../lib/cn";
import { MarkdownRenderer } from "../../lib/markdown";
import { AssistantStreaming } from "./AssistantStreaming";

interface MessageBubbleProps {
  role: "user" | "assistant";
  content: string;
  pending?: boolean;
}

export function MessageBubble({ role, content, pending }: MessageBubbleProps) {
  if (role === "user") {
    return (
      <div className="flex justify-end">
        <div
          className={cn(
            "max-w-[75%] rounded-2xl bg-secondary px-4 py-2 text-sm text-secondary-foreground",
          )}
        >
          {content}
        </div>
      </div>
    );
  }

  return (
    <div className="w-full rounded-lg border border-border/50 bg-card/40 px-4 py-3">
      {content ? <MarkdownRenderer content={content} /> : null}
      {pending ? <AssistantStreaming /> : null}
    </div>
  );
}
