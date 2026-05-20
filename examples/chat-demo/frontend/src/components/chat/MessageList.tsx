import type { ChatMessage } from "../../store/chatStore";
import { ScrollArea } from "../ui/scroll-area";
import { MessageBubble } from "./MessageBubble";

interface MessageListProps {
  messages: ChatMessage[];
}

export function MessageList({ messages }: MessageListProps) {
  return (
    <ScrollArea className="h-[65vh] w-full rounded-lg border p-4">
      <div className="space-y-4">
        {messages.map((message) => (
          <MessageBubble key={message.id} message={message} />
        ))}
      </div>
    </ScrollArea>
  );
}
