import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { MessageBubble } from "../src/components/chat/MessageBubble";

describe("MessageBubble", () => {
  test("renders user message as plain text", () => {
    render(<MessageBubble role="user" content="`x` plain" />);
    expect(screen.getByText("`x` plain")).toBeInTheDocument();
    expect(document.querySelector("code")).toBeNull();
  });

  test("renders assistant markdown content", () => {
    render(<MessageBubble role="assistant" content={"`x`\n\n- a\n- b"} />);
    const list = screen.getByRole("list");
    expect(list).toBeInTheDocument();
    expect(screen.getByText("a")).toBeInTheDocument();
    expect(screen.getByText("b")).toBeInTheDocument();
    expect(document.querySelector("code")).toBeTruthy();
  });
});
