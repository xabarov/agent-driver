import type { ChatMessage } from "../../store/chatStore";

const CONTROL_TOOL_NAMES = new Set([
  "todo_write",
  "planning_state_update",
  "ask_user_question",
]);

function shouldRenderMessage(message: ChatMessage, messages: ChatMessage[]): boolean {
  if (message.role !== "tool" || !CONTROL_TOOL_NAMES.has(message.name)) {
    return true;
  }
  if (message.name === "ask_user_question") {
    return false;
  }
  let index = messages.indexOf(message) - 1;
  while (index >= 0) {
    const prior = messages[index];
    if (prior?.role === "assistant") {
      return !prior.planningSnapshot;
    }
    if (prior?.role === "user") {
      break;
    }
    index -= 1;
  }
  return true;
}
import { useChatStore } from "../../store/chatStore";
import { useChatScroll } from "../../hooks/useChatScroll";
import { MessageBubble } from "./MessageBubble";

interface MessageListProps {
  messages: ChatMessage[];
  onRetryAssistant?: (assistantId: string) => void;
}

export function MessageList({ messages, onRetryAssistant }: MessageListProps) {
  const streaming = useChatStore((state) => state.streaming);
  const viewportRef = useChatScroll(messages, streaming);

  return (
    <div ref={viewportRef} className="h-full overflow-y-auto px-1 pr-3">
      <div className="mx-auto max-w-5xl space-y-5 pb-3 pt-1">
        {messages
          .filter((message) => shouldRenderMessage(message, messages))
          .map((message) => (
            <MessageBubble key={message.id} message={message} onRetryAssistant={onRetryAssistant} />
          ))}
      </div>
    </div>
  );
}
