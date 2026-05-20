import { useEffect, useRef } from "react";

import type { ChatMessage } from "../store/chatStore";

export function useChatScroll(messages: ChatMessage[], streaming: boolean) {
  const viewportRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) {
      return;
    }
    viewport.scrollTop = viewport.scrollHeight;
  }, [messages, streaming]);

  const lastContent =
    messages.length > 0
      ? messages[messages.length - 1]?.role === "assistant"
        ? (messages[messages.length - 1] as { content?: string }).content
        : undefined
      : undefined;

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport || !streaming) {
      return;
    }
    viewport.scrollTop = viewport.scrollHeight;
  }, [lastContent, streaming]);

  return viewportRef;
}
