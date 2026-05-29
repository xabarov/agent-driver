import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ToolsPicker } from "../src/components/settings/ToolsPicker";
import * as api from "../src/lib/api";
import type { WorkspaceStatusView } from "../src/types/api";
import { useSettingsStore } from "../src/store/settingsStore";

afterEach(() => {
  cleanup();
  useSettingsStore.setState({ toolPreset: "safe" });
  vi.restoreAllMocks();
});

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const workspace: WorkspaceStatusView = {
  mode: "session",
  root: "/tmp/workspace/session_1",
  sessionId: "session_1",
  exists: true,
  fileCount: 3,
  sampleAvailable: true,
};

describe("ToolsPicker", () => {
  it("loads and shows tools for selected preset", async () => {
    vi.spyOn(api, "fetchTools").mockResolvedValue({
      tools: [{ name: "web_search", description: "search", risk: "low", sideEffect: "network", approvalMode: "auto" }],
      workspace,
    });
    renderWithClient(<ToolsPicker />);
    await waitFor(() => {
      expect(screen.getByText("web_search")).toBeInTheDocument();
    });
    expect(screen.getByText("1 tools enabled")).toBeInTheDocument();
    expect(api.fetchTools).toHaveBeenCalledWith("safe", undefined);
  });

  it("refetches when preset changes", async () => {
    const fetchTools = vi.spyOn(api, "fetchTools").mockResolvedValue({ tools: [], workspace });
    renderWithClient(<ToolsPicker />);
    await waitFor(() => expect(fetchTools).toHaveBeenCalled());
    fireEvent.click(screen.getAllByText("workspace")[0]!);
    await waitFor(() => expect(fetchTools).toHaveBeenCalledWith("workspace", undefined));
  });

  it("caps visible tool badges with expand", async () => {
    const names = Array.from({ length: 12 }, (_, index) => `tool_${index}`);
    vi.spyOn(api, "fetchTools").mockResolvedValue({
      tools: names.map((name) => ({
        name,
        description: name,
        risk: "low",
        sideEffect: "none",
        approvalMode: "auto",
      })),
      workspace,
    });
    renderWithClient(<ToolsPicker />);
    await waitFor(() => expect(screen.getByText("12 tools enabled")).toBeInTheDocument());
    expect(screen.getByText("+4 more")).toBeInTheDocument();
    expect(screen.getByTestId("tools-picker-scroll")).toBeInTheDocument();
    fireEvent.click(screen.getByText("+4 more"));
    expect(screen.getByText("tool_11")).toBeInTheDocument();
  });
});
