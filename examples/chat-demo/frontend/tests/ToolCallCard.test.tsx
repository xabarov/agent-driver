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

  test("renders denied policy state", () => {
    render(
      <ToolCallCard
        message={{
          id: "tool_1",
          role: "tool",
          toolCallId: "call_1",
          name: "file_write",
          status: "denied",
          resultPreview: "force planning requires an approved plan",
        }}
      />,
    );
    expect(screen.getByText("file_write")).toBeInTheDocument();
    expect(screen.getByText("denied")).toBeInTheDocument();
    expect(screen.getByText("force planning requires an approved plan")).toBeInTheDocument();
  });

  test("shows completed result preview while collapsed", () => {
    render(
      <ToolCallCard
        message={{
          id: "tool_1",
          role: "tool",
          toolCallId: "call_1",
          name: "agent_tool",
          status: "done",
          resultPreview: "2 subagents completed",
        }}
      />,
    );
    expect(screen.getByText("agent_tool")).toBeInTheDocument();
    expect(screen.getByText("2 subagents completed")).toBeInTheDocument();
  });
});
