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
    expect(
      screen.getByText("force planning requires an approved plan"),
    ).toBeInTheDocument();
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

    fireEvent.click(
      screen.getByRole("button", { name: /inspect delegated subagent work/i }),
    );

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

    expect(screen.getByLabelText("Python execution")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Python execution done");
    expect(screen.getByText("Python calculation")).toBeInTheDocument();
    expect(screen.getByText("done")).toBeInTheDocument();
    expect(screen.getAllByText("3").length).toBeGreaterThan(0);
    expect(screen.getByLabelText("Python result summary")).toHaveTextContent("exact");
    expect(screen.getByText("from collections import Counter")).toBeInTheDocument();
    expect(screen.queryByText("Debug payload")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /expand python execution/i }));

    expect(screen.getByText("Code")).toBeInTheDocument();
    expect(screen.getAllByText(/Counter\('strawberry'\)/)).toHaveLength(2);
    expect(screen.getByText("Sandboxed Python")).toBeInTheDocument();
    expect(screen.getByText("calc_1")).toBeInTheDocument();
    expect(screen.getByText("Debug payload")).toBeInTheDocument();
  });

  test("renders python policy errors without raw JSON scroll", () => {
    render(
      <ToolCallCard
        message={{
          id: "tool_1",
          role: "tool",
          toolCallId: "call_1",
          name: "python",
          status: "failed",
          args: {
            code: "import os\nprint(os.getcwd())",
            session_id: "policy_recovery_demo",
          },
          resultPreview:
            "python policy: imports blocked by sandbox (os). Use allowed imports only.",
          durationMs: 17,
        }}
      />,
    );

    expect(screen.getByText("Python calculation")).toBeInTheDocument();
    expect(screen.getByText("failed")).toBeInTheDocument();
    expect(screen.getByText(/imports blocked by sandbox/)).toBeInTheDocument();
    expect(screen.getByText("Code")).toBeInTheDocument();
    expect(screen.queryByText('"code"')).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Python result summary")).not.toBeInTheDocument();
  });

  test("renders rounded python result chips for approximate numeric output", () => {
    render(
      <ToolCallCard
        message={{
          id: "tool_1",
          role: "tool",
          toolCallId: "call_1",
          name: "python",
          status: "done",
          args: {
            code: "import math\nprint(math.exp(-7.05))",
          },
          resultPreview: "result: ≈0.0008674",
        }}
      />,
    );

    const summary = screen.getByLabelText("Python result summary");
    expect(summary).toHaveTextContent("rounded");
    expect(summary).toHaveTextContent("≈0.0008674");
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

    fireEvent.click(
      screen.getByRole("button", { name: /inspect delegated subagent work/i }),
    );
    expect(screen.getByText("Child results")).toBeInTheDocument();
    expect(screen.getByText("Used tools")).toBeInTheDocument();
    expect(screen.getByText("memory")).toBeInTheDocument();
  });

  test("renders web search as an evidence panel with debug payload collapsed", () => {
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
    expect(screen.getByText("Web search")).toBeInTheDocument();
    expect(screen.getByText("web_search")).toBeInTheDocument();
    expect(screen.getByText("Fender history")).toBeInTheDocument();
    expect(screen.queryByText("Debug payload")).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: /inspect web search evidence/i }),
    );

    expect(screen.getByText("Debug payload")).toBeInTheDocument();
    expect(
      within(screen.getByText("Debug payload").parentElement!).getByText(
        /Fender history/,
      ),
    ).toBeInTheDocument();
  });

  test("renders web fetch as a page evidence panel", () => {
    render(
      <ToolCallCard
        message={{
          id: "tool_1",
          role: "tool",
          toolCallId: "call_1",
          name: "web_fetch",
          status: "done",
          args: { url: "https://example.com/fender" },
          resultPreview: "Fetched page about Fender history.",
          sources: [
            {
              id: "web_fetch:call_1:1",
              url: "https://example.com/fender",
              canonicalUrl: "https://example.com/fender",
              sourceType: "web_fetch",
              title: "Fender page",
              domain: "example.com",
              rank: 1,
            },
          ],
        }}
      />,
    );

    expect(screen.getByText("Web fetch")).toBeInTheDocument();
    expect(screen.getByText("web_fetch")).toBeInTheDocument();
    expect(screen.getAllByText("example.com").length).toBeGreaterThan(0);
    expect(screen.getByText("Fetched page about Fender history.")).toBeInTheDocument();
    expect(screen.queryByText("Debug payload")).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: /inspect web fetch evidence/i }),
    );

    expect(screen.getByLabelText("Sources")).toBeInTheDocument();
    expect(screen.getByText("Fender page")).toBeInTheDocument();
    expect(screen.getByText("fetched")).toBeInTheDocument();
    expect(screen.getByText("Debug payload")).toBeInTheDocument();
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
    expect(screen.getByText("Debug payload")).toBeInTheDocument();

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

    expect(screen.queryByText("Debug payload")).not.toBeInTheDocument();
    expect(screen.getByText("6 results")).toBeInTheDocument();
  });
});
