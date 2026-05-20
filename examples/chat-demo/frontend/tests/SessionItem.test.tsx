import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { SessionItem } from "../src/components/sessions/SessionItem";

const mutateAsync = vi.fn().mockResolvedValue({ ok: true });

vi.mock("../src/lib/sessions", () => ({
  useDeleteSession: () => ({
    mutateAsync,
    isPending: false,
  }),
}));

const session = {
  session_id: "session_1",
  thread_id: "thread_1",
  title: "My chat",
  updated_at: "2026-05-20T00:00:00Z",
  runs_count: 2,
};

describe("SessionItem", () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    mutateAsync.mockClear();
  });

  test("renders title and runs count", () => {
    const queryClient = new QueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/sessions/new"]}>
          <Routes>
            <Route path="/sessions/new" element={<SessionItem session={session} />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getByText("My chat")).toBeInTheDocument();
    expect(screen.getByText("2 runs")).toBeInTheDocument();
  });

  test("marks active session by link target", () => {
    const queryClient = new QueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/sessions/session_1"]}>
          <Routes>
            <Route path="/sessions/:id" element={<SessionItem session={session} />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const title = screen.getByText("My chat");
    expect(title.closest("div")?.className).toContain("border-primary");
  });

  test("delete flow opens confirm and calls mutation", async () => {
    const queryClient = new QueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/sessions/new"]}>
          <Routes>
            <Route path="/sessions/new" element={<SessionItem session={session} />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByLabelText("Delete session My chat"));

    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(screen.getByText(/cannot be undone/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));

    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledWith("session_1");
    });
  });
});
