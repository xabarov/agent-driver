import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { MessageBubble } from "../src/components/chat/MessageBubble";
import { TooltipProvider } from "../src/components/ui/tooltip";

function renderBubble(ui: React.ReactElement) {
  return render(<TooltipProvider>{ui}</TooltipProvider>);
}

describe("MessageBubble", () => {
  test("renders user message as plain text", () => {
    renderBubble(<MessageBubble message={{ id: "u1", role: "user", content: "`x` plain" }} />);
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
    const link = screen.getByRole("link", { name: "example" });
    expect(link).toHaveAttribute("href", "https://example.com");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
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
});
