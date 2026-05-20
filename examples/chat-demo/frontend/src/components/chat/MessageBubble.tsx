import { cn } from "../../lib/cn";
import { MarkdownRenderer } from "../../lib/markdown";
import type { ChatMessage } from "../../store/chatStore";
import { AssistantStreaming } from "./AssistantStreaming";
import { ToolCallCard } from "./ToolCallCard";

interface MessageBubbleProps {
  message: ChatMessage;
}

export function MessageBubble({ message }: MessageBubbleProps) {
  if (message.role === "tool") {
    return <ToolCallCard message={message} />;
  }

  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div
          className={cn(
            "max-w-[75%] rounded-2xl bg-secondary px-4 py-2 text-sm text-secondary-foreground",
          )}
        >
          {message.content}
        </div>
      </div>
    );
  }

  return (
    <div className="w-full rounded-lg border border-border/50 bg-card/40 px-4 py-3">
      {message.content ? (
        message.pending ? (
          <div className="whitespace-pre-wrap text-sm leading-relaxed">{message.content}</div>
        ) : (
          <MarkdownRenderer content={message.content} />
        )
      ) : null}
      {message.pending ? <AssistantStreaming /> : null}
    </div>
  );
}
