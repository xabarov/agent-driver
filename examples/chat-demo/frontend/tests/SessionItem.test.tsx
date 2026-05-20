import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { SessionItem } from "../src/components/sessions/SessionItem";

describe("SessionItem", () => {
  test("renders title and runs count", () => {
    const queryClient = new QueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/sessions/new"]}>
          <Routes>
            <Route
              path="/sessions/new"
              element={
                <SessionItem
                  session={{
                    session_id: "session_1",
                    thread_id: "thread_1",
                    title: "My chat",
                    updated_at: "2026-05-20T00:00:00Z",
                    runs_count: 2,
                  }}
                />
              }
            />
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
            <Route
              path="/sessions/:id"
              element={
                <SessionItem
                  session={{
                    session_id: "session_1",
                    thread_id: "thread_1",
                    title: "Active chat",
                    updated_at: "2026-05-20T00:00:00Z",
                    runs_count: 1,
                  }}
                />
              }
            />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const title = screen.getByText("Active chat");
    expect(title.closest("div")?.className).toContain("border-primary");
  });
});
