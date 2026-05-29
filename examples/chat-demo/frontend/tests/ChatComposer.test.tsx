import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { ChatComposer } from "../src/components/chat/ChatComposer";

describe("ChatComposer", () => {
  test("sends a normal message when idle", () => {
    const onSend = vi.fn();
    const onSteer = vi.fn();
    render(
      <ChatComposer
        streaming={false}
        onSend={onSend}
        onSteer={onSteer}
        onStop={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText("Message the assistant…"), {
      target: { value: "hello" },
    });
    fireEvent.click(screen.getByLabelText("Send"));

    expect(onSend).toHaveBeenCalledWith("hello");
    expect(onSteer).not.toHaveBeenCalled();
  });

  test("queues steering message while streaming", () => {
    const onSend = vi.fn();
    const onSteer = vi.fn();
    render(
      <ChatComposer
        streaming
        onSend={onSend}
        onSteer={onSteer}
        onStop={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText("Steer the running assistant…"), {
      target: { value: "prefer concise answer" },
    });
    fireEvent.click(screen.getByLabelText("Queue steering message"));

    expect(onSteer).toHaveBeenCalledWith("prefer concise answer");
    expect(onSend).not.toHaveBeenCalled();
  });
});
