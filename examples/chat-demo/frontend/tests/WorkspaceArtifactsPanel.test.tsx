import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { WorkspaceArtifactsPanel } from "../src/components/chat/WorkspaceArtifactsPanel";
import * as api from "../src/lib/api";

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("WorkspaceArtifactsPanel", () => {
  test("lists artifacts and loads report preview", async () => {
    vi.spyOn(api, "fetchWorkspaceArtifacts").mockResolvedValue({
      ok: true,
      sessionId: "session_1",
      artifacts: [
        {
          path: "research/report.md",
          kind: "report",
          sizeBytes: 1024,
          modifiedAt: "2026-05-31T12:00:00Z",
        },
      ],
    });
    vi.spyOn(api, "fetchWorkspaceArtifactPreview").mockResolvedValue({
      ok: true,
      sessionId: "session_1",
      path: "research/report.md",
      kind: "report",
      sizeBytes: 1024,
      content: "# Report\n\nA durable draft.",
      truncated: false,
    });

    renderWithClient(<WorkspaceArtifactsPanel sessionId="session_1" />);

    fireEvent.click(screen.getByRole("button", { name: /artifacts/i }));

    expect(await screen.findAllByText("research/report.md")).not.toHaveLength(0);
    expect(await screen.findByText(/durable draft/i)).toBeInTheDocument();
  });

  test("uses known report artifact while workspace query is stale", async () => {
    vi.spyOn(api, "fetchWorkspaceArtifacts").mockResolvedValue({
      ok: true,
      sessionId: "session_1",
      artifacts: [],
    });
    vi.spyOn(api, "fetchWorkspaceArtifactPreview").mockRejectedValue(
      new Error("not indexed yet"),
    );

    renderWithClient(
      <WorkspaceArtifactsPanel
        sessionId="session_1"
        knownArtifacts={[
          {
            path: "research/report.md",
            kind: "report",
            sizeBytes: 2048,
            modifiedAt: "2026-06-02T12:00:00Z",
          },
        ]}
      />,
    );

    expect(
      await screen.findByRole("button", { name: /artifacts \(1\)/i }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /artifacts/i }));

    expect(await screen.findAllByText("research/report.md")).not.toHaveLength(0);
  });
});
