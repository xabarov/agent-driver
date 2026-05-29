import { useEffect, useRef } from "react";

import type { ChatMessage } from "../store/chatStore";

const NEAR_BOTTOM_PX = 96;

function isNearBottom(viewport: HTMLDivElement): boolean {
  const distance = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
  return distance <= NEAR_BOTTOM_PX;
}

function scrollToBottom(viewport: HTMLDivElement): void {
  viewport.scrollTop = viewport.scrollHeight;
}

export function useChatScroll(messages: ChatMessage[], streaming: boolean) {
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const pinnedToBottomRef = useRef(true);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) {
      return;
    }
    const onScroll = () => {
      pinnedToBottomRef.current = isNearBottom(viewport);
    };
    viewport.addEventListener("scroll", onScroll, { passive: true });
    return () => viewport.removeEventListener("scroll", onScroll);
  }, []);

  const lastContent =
    messages.length > 0
      ? messages[messages.length - 1]?.role === "assistant"
        ? (messages[messages.length - 1] as { content?: string }).content
        : undefined
      : undefined;

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) {
      return;
    }
    if (pinnedToBottomRef.current || isNearBottom(viewport)) {
      scrollToBottom(viewport);
      pinnedToBottomRef.current = true;
    }
  }, [messages, streaming, lastContent]);

  return viewportRef;
}
