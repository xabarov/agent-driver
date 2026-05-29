import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => cleanup());

import { InterruptCard } from "../src/components/chat/InterruptCard";
import type { PendingInterrupt } from "../src/store/chatStore";

const base: PendingInterrupt = {
  runId: "run-1",
  interruptId: "int-1",
  reason: "tool_approval",
  proposedAction: { tool: "shell" },
  allowedActions: ["approve", "reject"],
};

describe("InterruptCard", () => {
  it("shows only allowed action buttons", () => {
    const onAction = vi.fn();
    render(<InterruptCard interrupt={base} onAction={onAction} />);
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
  });

  it("disables resume when interrupt id is missing", () => {
    render(
      <InterruptCard interrupt={{ ...base, interruptId: "" }} onAction={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
  });

  it("renders clarification interrupts with explicit heading and requires text", () => {
    const onAction = vi.fn();
    render(
      <InterruptCard
        interrupt={{
          ...base,
          reason: "clarification_required",
          title: "User clarification required",
          allowedActions: ["clarify", "cancel"],
        }}
        onAction={onAction}
      />,
    );

    expect(screen.getByText("Clarification required")).toBeInTheDocument();
    expect(screen.queryByText("ask_user_question")).not.toBeInTheDocument();
    const button = screen.getByRole("button", { name: "Send clarification" });
    expect(button).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "описание моделей" },
    });
    fireEvent.click(button);
    expect(onAction).toHaveBeenCalledWith({
      action: "clarify",
      message: "описание моделей",
    });
  });

  it("renders plan approval payload and submits edited plan content", () => {
    const onAction = vi.fn();
    render(
      <InterruptCard
        interrupt={{
          ...base,
          reason: "plan_approval_required",
          title: "Approve plan?",
          proposedAction: {
            args: { reason: "ready" },
            plan_approval: {
              plan_id: "plan_1",
              content: "1. Inspect\n2. Implement",
              content_hash: "abc123",
              path: "/tmp/plan.md",
            },
          },
          allowedActions: ["approve", "edit", "reject", "cancel"],
        }}
        onAction={onAction}
      />,
    );

    expect(screen.getByText("Plan approval required")).toBeInTheDocument();
    expect(screen.getByText("/tmp/plan.md")).toBeInTheDocument();
    expect(screen.getByText("abc123")).toBeInTheDocument();

    const textarea = screen.getByRole("textbox");
    fireEvent.change(textarea, { target: { value: "1. Inspect\n2. Verify" } });
    fireEvent.click(screen.getByRole("button", { name: "Submit plan edit" }));

    expect(onAction).toHaveBeenCalledWith({
      action: "edit",
      editedToolArgs: {
        reason: "ready",
        content: "1. Inspect\n2. Verify",
      },
    });
  });
});
