import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MarkdownRenderer } from "../src/lib/markdown";

describe("MarkdownRenderer", () => {
  it("uses light prose by default and inverts only in dark mode", () => {
    render(<MarkdownRenderer content="Readable assistant text" />);

    const prose = screen.getByText("Readable assistant text").parentElement;
    expect(prose).toHaveClass("prose");
    expect(prose).toHaveClass("dark:prose-invert");
    expect(prose).not.toHaveClass("prose-invert");
  });
});
