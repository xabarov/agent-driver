import { useRunStream } from "../../hooks/useRunStream";
import { useChatStore } from "../../store/chatStore";
import { Card, CardContent } from "../ui/card";
import { EmptyState } from "./EmptyState";
import { ComposerInput } from "./ComposerInput";
import { MessageList } from "./MessageList";

export function ChatPage() {
  const messages = useChatStore((state) => state.messages);
  const streaming = useChatStore((state) => state.streaming);
  const { sendMessage, stopStreaming } = useRunStream();

  return (
    <Card className="h-full">
      <CardContent className="space-y-4 p-4">
        {messages.length === 0 ? <EmptyState /> : <MessageList messages={messages} />}
        <ComposerInput
          streaming={streaming}
          onSend={(text) => {
            void sendMessage(text);
          }}
          onStop={stopStreaming}
        />
      </CardContent>
    </Card>
  );
}
