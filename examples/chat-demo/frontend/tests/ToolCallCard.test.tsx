import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { ToolCallCard } from "../src/components/chat/ToolCallCard";

describe("ToolCallCard", () => {
  test("renders tool name and status", () => {
    render(
      <ToolCallCard
        message={{
          id: "tool_1",
          role: "tool",
          toolCallId: "call_1",
          name: "web_search",
          status: "running",
          argsSummary: '{"query":"demo"}',
        }}
      />,
    );
    expect(screen.getByText("web_search")).toBeInTheDocument();
    expect(screen.getByText("running")).toBeInTheDocument();
  });
});
