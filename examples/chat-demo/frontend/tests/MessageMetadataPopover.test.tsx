import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { MessageMetadataPopover } from "../src/components/chat/MessageMetadataPopover";

describe("MessageMetadataPopover", () => {
  test("renders metadata rows when usage is present", () => {
    render(
      <MessageMetadataPopover
        metadata={{
          promptTokens: 100,
          completionTokens: 678,
          totalTokens: 778,
          durationMs: 5300,
          tokensPerSecond: 127.9,
          costUsd: 0.0225145,
          provider: "openrouter",
        }}
      />,
    );
    fireEvent.click(screen.getByLabelText("Metadata"));
    expect(screen.getByText("Tokens per second")).toBeInTheDocument();
    expect(screen.getByText("Token count")).toBeInTheDocument();
    expect(screen.getByText("Cost")).toBeInTheDocument();
    expect(screen.getByText("Duration")).toBeInTheDocument();
    expect(screen.getByText("778 tokens")).toBeInTheDocument();
    expect(screen.getByText("$0.0225145")).toBeInTheDocument();
    expect(screen.getByText("5.3s")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "activity" })).toHaveAttribute(
      "href",
      "https://openrouter.ai/activity",
    );
  });
});
