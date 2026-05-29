import { AlertTriangle, Bot, User } from "lucide-react";

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
  // Surface the planning-mode fabrication verdict (see
  // `agent_driver.runtime.planning_check`). When the agent wrote a plan
  // via todo_write but never invoked a data tool, the prose answer is
  // almost certainly fabricated. We show a small amber warning above the
  // assistant bubble for that case only.
  const planningFabricated =
    !message.pending && message.metadata?.planningExecuted === "fabricated";

  return (
    <div className="group flex gap-3 outline-none" tabIndex={-1}>
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/15">
        <Bot className="h-4 w-4 text-primary" />
      </div>
      <div className="relative min-w-0 max-w-[92%] flex-1">
        {planningFabricated ? (
          <div
            role="alert"
            className={cn(
              "mb-2 flex items-start gap-2 rounded-md border px-3 py-2 text-xs",
              "border-amber-300 bg-amber-50 text-amber-900",
              "dark:border-amber-700 dark:bg-amber-900/20 dark:text-amber-200",
            )}
          >
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
            <span>
              The agent wrote a plan but never invoked a data tool to execute
              it — the answer below is likely fabricated. Try re-asking with
              an explicit instruction to use the tools.
            </span>
          </div>
        ) : null}
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
