import { Bot, User } from "lucide-react";

import { cn } from "../../lib/cn";
import { MarkdownRenderer } from "../../lib/markdown";
import type { ChatMessage } from "../../store/chatStore";
import { useChatStore } from "../../store/chatStore";
import { AssistantStreaming } from "./AssistantStreaming";
import { MessageActions } from "./MessageActions";
import { PlanningCard } from "./PlanningCard";
import { ToolCallCard } from "./ToolCallCard";

interface MessageBubbleProps {
  message: ChatMessage;
  onRetryAssistant?: (assistantId: string) => void;
}

export function MessageBubble({ message, onRetryAssistant }: MessageBubbleProps) {
  const streaming = useChatStore((state) => state.streaming);
  const deleteMessage = useChatStore((state) => state.deleteMessage);

  if (message.role === "tool") {
    return <ToolCallCard message={message} />;
  }

  if (message.role === "user") {
    return (
      <div
        className="group flex justify-end gap-3 outline-none"
        tabIndex={-1}
      >
        <div className="flex min-w-0 max-w-[85%] flex-col items-end">
          <div
            className={cn(
              "rounded-2xl px-4 py-2.5 text-sm leading-relaxed",
              "bg-[hsl(var(--chat-user-bg))] text-[hsl(var(--chat-user-fg))]",
            )}
          >
            {message.content}
          </div>
          <MessageActions
            content={message.content}
            align="end"
            disabled={streaming}
            onDelete={() => deleteMessage(message.id)}
          />
        </div>
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-secondary">
          <User className="h-4 w-4 text-muted-foreground" />
        </div>
      </div>
    );
  }

  const showActions = !message.pending && Boolean(message.content || message.metadata);

  return (
    <div className="group flex gap-3 outline-none" tabIndex={-1}>
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/15">
        <Bot className="h-4 w-4 text-primary" />
      </div>
      <div className="relative min-w-0 max-w-[92%] flex-1">
        <div
          className={cn(
            "rounded-2xl px-4 py-3 text-sm leading-relaxed",
            "bg-[hsl(var(--chat-assistant-bg))]",
          )}
        >
          {message.content ? (
            message.pending ? (
              <div className="whitespace-pre-wrap">{message.content}</div>
            ) : (
              <MarkdownRenderer content={message.content} />
            )
          ) : null}
          {message.planningSnapshot ? (
            <PlanningCard snapshot={message.planningSnapshot} streaming={message.pending} />
          ) : null}
          {message.pending ? <AssistantStreaming /> : null}
        </div>
        {showActions ? (
          <MessageActions
            content={message.content}
            metadata={message.metadata}
            showRetry
            showMetadata
            disabled={streaming}
            onRetry={() => onRetryAssistant?.(message.id)}
            onDelete={() => deleteMessage(message.id)}
          />
        ) : null}
      </div>
    </div>
  );
}
