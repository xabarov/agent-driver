import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ToolsPicker } from "../src/components/settings/ToolsPicker";
import { useSettingsStore } from "../src/store/settingsStore";

afterEach(() => {
  cleanup();
  useSettingsStore.setState({ toolPreset: "web" });
  vi.restoreAllMocks();
});

describe("ToolsPicker", () => {
  it("shows only user-facing web tool toggles", () => {
    render(<ToolsPicker />);
    expect(screen.getByText("Web Search")).toBeInTheDocument();
    expect(screen.getByText("Web Fetch")).toBeInTheDocument();
    expect(screen.getByText(/agent can use planning/i)).toBeInTheDocument();
    expect(screen.queryByText(/todo_write/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/grep/i)).not.toBeInTheDocument();
  });

  it("maps web search/fetch toggles to presets", async () => {
    render(<ToolsPicker />);
    const [search, fetch] = screen.getAllByRole("checkbox");
    expect(search).toBeChecked();
    expect(fetch).toBeChecked();

    fireEvent.click(fetch!);
    await waitFor(() => {
      expect(useSettingsStore.getState().toolPreset).toBe("web_search");
    });

    fireEvent.click(search!);
    await waitFor(() => {
      expect(useSettingsStore.getState().toolPreset).toBe("off");
    });

    fireEvent.click(fetch!);
    await waitFor(() => {
      expect(useSettingsStore.getState().toolPreset).toBe("web_fetch");
    });
  });

  it("normalizes legacy presets loaded from local storage", () => {
    useSettingsStore.setState({ toolPreset: "safe" as never });
    render(<ToolsPicker />);
    expect(screen.getByText("Web")).toBeInTheDocument();
  });
});
