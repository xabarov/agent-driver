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

  test("renders agent tool as delegated work panel", () => {
    render(
      <ToolCallCard
        message={{
          id: "tool_1",
          role: "tool",
          toolCallId: "call_1",
          name: "agent_tool",
          status: "done",
          args: {
            description: "Verify Fender model timeline",
            task: "Check key Fender Jazzmaster facts and return a short summary.",
            task_type: "verifier",
            execution_mode: "sync",
          },
          resultPreview: "2 subagents completed",
        }}
      />,
    );
    expect(screen.getByText("Delegated work")).toBeInTheDocument();
    expect(screen.getByText("joined")).toBeInTheDocument();
    expect(screen.getByText("Verify Fender model timeline")).toBeInTheDocument();
    expect(screen.getByText("verifier")).toBeInTheDocument();
    expect(screen.getByText("2 subagents completed")).toBeInTheDocument();
    expect(screen.queryByText("Debug payload")).not.toBeInTheDocument();
  });

  test("keeps agent tool raw payload behind inspect", () => {
    render(
      <ToolCallCard
        message={{
          id: "tool_1",
          role: "tool",
          toolCallId: "call_1",
          name: "agent_tool",
          status: "done",
          args: {
            description: "Research Jazzmaster facts",
            task: "Return 3 verified facts.",
          },
          resultPreview: "child completed",
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /inspect delegated subagent work/i }));

    expect(screen.getByText("Child brief")).toBeInTheDocument();
    expect(screen.getByText("Return 3 verified facts.")).toBeInTheDocument();
    expect(screen.getByText("Debug payload")).toBeInTheDocument();
  });

  test("renders python execution as a focused calculation panel", () => {
    render(
      <ToolCallCard
        message={{
          id: "tool_1",
          role: "tool",
          toolCallId: "call_1",
          name: "python",
          status: "done",
          args: {
            code: "from collections import Counter\nprint(Counter('strawberry')['r'])",
            session_id: "calc_1",
          },
          resultPreview: "3",
          durationMs: 42,
        }}
      />,
    );

    expect(screen.getByText("Python calculation")).toBeInTheDocument();
    expect(screen.getByText("done")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("from collections import Counter")).toBeInTheDocument();
    expect(screen.queryByText("Debug payload")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /expand python execution/i }));

    expect(screen.getByText("Code")).toBeInTheDocument();
    expect(screen.getAllByText(/Counter\('strawberry'\)/)).toHaveLength(2);
    expect(screen.getByText("Sandboxed Python")).toBeInTheDocument();
    expect(screen.getByText("calc_1")).toBeInTheDocument();
    expect(screen.getByText("Debug payload")).toBeInTheDocument();
  });

  test("shows subagent child lifecycle rows", () => {
    render(
      <ToolCallCard
        message={{
          id: "tool_1",
          role: "tool",
          toolCallId: "call_1",
          name: "agent_tool",
          status: "done",
          args: {
            description: "Compare candidates",
            task: "Compare two candidates and return a short verdict.",
          },
          subagent: {
            groupId: "group_1",
            groupStatus: "joined",
            childRuns: [
              {
                taskId: "task_1",
                childRunId: "run_child",
                status: "completed",
                description: "Verifier",
                outputPreview: "candidate A is safer",
                usedTools: ["memory"],
              },
            ],
          },
        }}
      />,
    );

    expect(screen.getByText("joined")).toBeInTheDocument();
    expect(screen.getByLabelText("Child agent Verifier completed")).toBeInTheDocument();
    expect(screen.getByText("run_child")).toBeInTheDocument();
    expect(screen.getByText("candidate A is safer")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /inspect delegated subagent work/i }));
    expect(screen.getByText("Child results")).toBeInTheDocument();
    expect(screen.getByText("Used tools")).toBeInTheDocument();
    expect(screen.getByText("memory")).toBeInTheDocument();
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
