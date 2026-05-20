import { ScrollArea } from "../ui/scroll-area";
import { MessageBubble } from "./MessageBubble";

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  pending?: boolean;
}

interface MessageListProps {
  messages: Message[];
}

export function MessageList({ messages }: MessageListProps) {
  return (
    <ScrollArea className="h-[65vh] w-full rounded-lg border p-4">
      <div className="space-y-4">
        {messages.map((message) => (
          <MessageBubble
            key={message.id}
            role={message.role}
            content={message.content}
            pending={message.pending}
          />
        ))}
      </div>
    </ScrollArea>
  );
}
