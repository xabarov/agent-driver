import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { MessageBubble } from "../src/components/chat/MessageBubble";
import { TooltipProvider } from "../src/components/ui/tooltip";

function renderBubble(ui: React.ReactElement) {
  return render(<TooltipProvider>{ui}</TooltipProvider>);
}

describe("MessageBubble", () => {
  afterEach(() => {
    cleanup();
  });

  test("renders user message as plain text", () => {
    renderBubble(
      <MessageBubble message={{ id: "u1", role: "user", content: "`x` plain" }} />,
    );
    expect(screen.getByText("`x` plain")).toBeInTheDocument();
    expect(document.querySelector("code")).toBeNull();
  });

  test("renders assistant markdown content", () => {
    renderBubble(
      <MessageBubble
        message={{ id: "a1", role: "assistant", content: "`x`\n\n- a\n- b" }}
      />,
    );
    const list = screen.getByRole("list");
    expect(list).toBeInTheDocument();
    expect(screen.getByText("a")).toBeInTheDocument();
    expect(screen.getByText("b")).toBeInTheDocument();
    expect(document.querySelector("code")).toBeTruthy();
  });

  test("renders external markdown links in a new tab", () => {
    renderBubble(
      <MessageBubble
        message={{
          id: "a2",
          role: "assistant",
          content: "See [example](https://example.com) for details.",
        }}
      />,
    );
    const link = screen.getAllByRole("link", { name: "example" })[0];
    expect(link).toHaveAttribute("href", "https://example.com");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByLabelText("Sources")).toBeInTheDocument();
    expect(screen.getByText("example.com")).toBeInTheDocument();
  });

  test("highlights fenced python blocks", () => {
    renderBubble(
      <MessageBubble
        message={{
          id: "a3",
          role: "assistant",
          content: "```python\ndef foo():\n    pass\n```",
        }}
      />,
    );
    const highlighted = document.querySelector("code.hljs, code.language-python");
    expect(highlighted).toBeTruthy();
    expect(highlighted?.textContent).toContain("def foo");
  });

  test("renders markdown while assistant message is pending", () => {
    renderBubble(
      <MessageBubble
        message={{
          id: "a4",
          role: "assistant",
          content: "Streaming `python` text",
          pending: true,
        }}
      />,
    );

    expect(document.querySelector("code")).toHaveTextContent("python");
    expect(screen.getByText("Writing")).toBeInTheDocument();
  });

  test("renders compaction notice", () => {
    renderBubble(
      <MessageBubble
        message={{
          id: "c1",
          role: "compaction",
          compactionId: "compact_1",
          status: "done",
          mode: "partial",
          summarizedMessageCount: 6,
        }}
      />,
    );

    expect(screen.getByText("Conversation memory compacted")).toBeInTheDocument();
    expect(
      screen.getByText("Older context was summarized across 6 messages."),
    ).toBeInTheDocument();
  });

  test("renders tool-derived source evidence", () => {
    renderBubble(
      <MessageBubble
        message={{
          id: "a5",
          role: "assistant",
          content: "Fetched answer.",
          sources: [
            {
              id: "web_fetch:call_1:1",
              url: "https://example.com/fender",
              canonicalUrl: "https://example.com/fender",
              sourceType: "web_fetch",
              title: "Fender history",
              domain: "example.com",
              excerpt: "Fetched page excerpt",
              toolCallId: "call_1",
              rank: 1,
            },
          ],
        }}
      />,
    );

    expect(screen.getByLabelText("Sources")).toBeInTheDocument();
    expect(screen.getByText("Fender history")).toBeInTheDocument();
    expect(screen.getByText("fetched")).toBeInTheDocument();
    expect(screen.getByText(/1 fetched/)).toBeInTheDocument();
    expect(screen.getByText(/1 domains/)).toBeInTheDocument();
  });

  test("source cards are keyboard focusable links", () => {
    renderBubble(
      <MessageBubble
        message={{
          id: "a6",
          role: "assistant",
          content: "See [docs](https://example.com/docs).",
        }}
      />,
    );

    const sourceLink = screen.getAllByRole("link", { name: /docs/i }).at(-1);
    expect(sourceLink).toBeDefined();
    if (!sourceLink) {
      return;
    }
    sourceLink.focus();

    expect(sourceLink).toHaveFocus();
    expect(sourceLink).toHaveAttribute("target", "_blank");
    expect(sourceLink).toHaveAttribute("rel", "noopener noreferrer");
  });
});
