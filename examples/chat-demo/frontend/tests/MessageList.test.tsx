import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test } from "vitest";

import { MessageList } from "../src/components/chat/MessageList";
import { TooltipProvider } from "../src/components/ui/tooltip";
import { useChatStore, type ChatMessage } from "../src/store/chatStore";

function renderList(messages: ChatMessage[]) {
  return render(
    <TooltipProvider>
      <div style={{ height: 600 }}>
        <MessageList messages={messages} />
      </div>
    </TooltipProvider>,
  );
}

describe("MessageList compaction accessibility", () => {
  beforeEach(() => {
    useChatStore.getState().reset();
  });

  afterEach(() => {
    cleanup();
  });

  test("marks message region busy while compaction is running", () => {
    renderList([
      {
        id: "c1",
        role: "compaction",
        compactionId: "compact_1",
        status: "running",
      },
    ]);

    expect(screen.getByText("Compacting conversation memory")).toBeInTheDocument();
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(screen.getByRole("status").closest(".chat-scrollbar")).toHaveAttribute(
      "aria-busy",
      "true",
    );
  });

  test("details are reachable and toggleable", () => {
    renderList([
      {
        id: "c1",
        role: "compaction",
        compactionId: "compact_1",
        status: "done",
        mode: "partial",
      },
    ]);

    const summary = screen.getByText("Details");
    summary.focus();
    expect(summary).toHaveFocus();
    fireEvent.click(summary);
    expect(screen.getByText("compact_1")).toBeVisible();
  });
});
