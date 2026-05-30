import { afterEach, describe, expect, test } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

import { ToolCallCard } from "../src/components/chat/ToolCallCard";

describe("ToolCallCard", () => {
  afterEach(() => {
    cleanup();
  });

  test("renders tool name and status", () => {
    render(
      <ToolCallCard
        message={{
          id: "tool_1",
          role: "tool",
          toolCallId: "call_1",
          name: "web_search",
          status: "running",
          argsSummary: "query: demo",
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

  test("wraps tool details behind a collapsible input section", () => {
    render(
      <ToolCallCard
        message={{
          id: "tool_1",
          role: "tool",
          toolCallId: "call_1",
          name: "web_search",
          status: "done",
          argsSummary: "query: Fender history",
          args: { query: "Fender history", max_results: 6 },
        }}
      />,
    );
    expect(screen.getByText("query: Fender history")).toBeInTheDocument();
    expect(screen.queryByText("Input")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /expand tool call web_search/i }));

    expect(screen.getByText("Input")).toBeInTheDocument();
    expect(within(screen.getByText("Input").parentElement!).getByText(/Fender history/)).toBeInTheDocument();
  });

  test("collapses details when a running tool completes", () => {
    const { rerender } = render(
      <ToolCallCard
        message={{
          id: "tool_1",
          role: "tool",
          toolCallId: "call_1",
          name: "web_search",
          status: "running",
          argsSummary: "query: demo",
          args: { query: "demo" },
        }}
      />,
    );
    expect(screen.getByText("Input")).toBeInTheDocument();

    rerender(
      <ToolCallCard
        message={{
          id: "tool_1",
          role: "tool",
          toolCallId: "call_1",
          name: "web_search",
          status: "done",
          argsSummary: "query: demo",
          args: { query: "demo" },
          resultPreview: "6 results",
        }}
      />,
    );

    expect(screen.queryByText("Input")).not.toBeInTheDocument();
    expect(screen.getByText("6 results")).toBeInTheDocument();
  });
});
