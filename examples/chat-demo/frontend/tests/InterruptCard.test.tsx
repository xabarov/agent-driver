import { cleanup, render, screen } from "@testing-library/react";
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
      <InterruptCard
        interrupt={{ ...base, interruptId: "" }}
        onAction={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
  });
});
