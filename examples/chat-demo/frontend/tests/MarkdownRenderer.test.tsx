import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MarkdownRenderer, StreamingMarkdownRenderer } from "../src/lib/markdown";

describe("MarkdownRenderer", () => {
  afterEach(() => {
    cleanup();
  });

  it("uses light prose by default and inverts only in dark mode", () => {
    render(<MarkdownRenderer content="Readable assistant text" />);

    const prose = screen.getByText("Readable assistant text").parentElement;
    expect(prose).toHaveClass("prose");
    expect(prose).toHaveClass("dark:prose-invert");
    expect(prose).not.toHaveClass("prose-invert");
  });

  it("renders math with KaTeX", () => {
    const { container } = render(
      <MarkdownRenderer content="$$P(X > 3) = e^{-7.05}$$" />,
    );

    expect(container.querySelector(".katex")).toBeInTheDocument();
    expect(screen.queryByText(/\$\$/)).not.toBeInTheDocument();
  });

  it("sanitizes raw html and unsafe links", () => {
    render(
      <MarkdownRenderer
        content={'<script>alert("x")</script>\n\n[bad](javascript:alert(1))'}
      />,
    );

    expect(screen.queryByText(/alert/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "bad" })).not.toBeInTheDocument();
    expect(screen.getByText("bad")).toBeInTheDocument();
  });

  it("renders code blocks with language labels and copy action", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    render(<MarkdownRenderer content={'```python\nprint("hello")\n```'} />);

    expect(screen.getByText("Python")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));

    expect(writeText).toHaveBeenCalledWith('print("hello")');
    expect(await screen.findByText("Copied")).toBeInTheDocument();
  });

  it("keeps long code lines inside the code block scroll area", () => {
    render(<MarkdownRenderer content={`\`\`\`python\n${"x".repeat(240)}\n\`\`\``} />);

    const block = document.querySelector("pre.hljs-block");
    expect(block).toBeInTheDocument();
    expect(block).toHaveClass("overflow-auto");
    expect(block).toHaveClass("max-h-[28rem]");
  });

  it("autolinks bare urls through GFM", () => {
    render(<MarkdownRenderer content="See https://example.com/docs." />);

    const link = screen.getByRole("link", { name: "https://example.com/docs" });
    expect(link).toHaveAttribute("href", "https://example.com/docs");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("keeps unfinished streaming code fences as plain tail text", () => {
    render(
      <StreamingMarkdownRenderer
        content={"Intro\n\n```python\nprint('still streaming')"}
      />,
    );

    expect(screen.getByText("Intro")).toBeInTheDocument();
    expect(screen.getByText(/```python/)).toBeInTheDocument();
    expect(screen.queryByText("Python")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copy" })).not.toBeInTheDocument();
  });

  it("keeps unfinished display math as plain tail text", () => {
    const { container } = render(
      <StreamingMarkdownRenderer content={"Before\n\n$$P(X > 3) = e^{-"} />,
    );

    expect(screen.getByText("Before")).toBeInTheDocument();
    expect(screen.getByText(/\$\$P\(X > 3\)/)).toBeInTheDocument();
    expect(container.querySelector(".katex")).not.toBeInTheDocument();
  });

  it("renders completed streaming markdown normally", () => {
    const { container } = render(
      <StreamingMarkdownRenderer content={"Done\n\n$$P(X > 3) = e^{-7.05}$$"} />,
    );

    expect(screen.getByText("Done")).toBeInTheDocument();
    expect(container.querySelector(".katex")).toBeInTheDocument();
  });
});
