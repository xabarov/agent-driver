import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Header } from "../src/components/layout/Header";
import { ThemeProvider } from "../src/components/layout/ThemeProvider";
import { TooltipProvider } from "../src/components/ui/tooltip";
import * as api from "../src/lib/api";
import { useChatStore } from "../src/store/chatStore";

function mockModelQueries() {
  vi.spyOn(api, "fetchProviders").mockResolvedValue({
    name: "openrouter",
    model: "qwen/qwen3-235b-a22b-2507",
    base_url: "https://openrouter.ai/api/v1",
    status: {
      provider_name: "openrouter",
      provider_kind: "openai_compatible",
      healthy: true,
      configured: true,
      latency_ms: 12,
      avg_latency_ms: 12,
      request_count: 1,
      error_count: 0,
    },
  });
  vi.spyOn(api, "fetchModels").mockResolvedValue({
    provider: "openrouter",
    models: [
      {
        id: "qwen/qwen3-235b-a22b-2507",
        name: null,
        description: null,
        context_length: null,
      },
    ],
  });
}

function renderHeader() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <TooltipProvider>
          <Header />
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  localStorage.clear();
  useChatStore.getState().reset();
  vi.restoreAllMocks();
});

describe("Header", () => {
  it("shows healthy provider status", async () => {
    mockModelQueries();
    vi.spyOn(api, "fetchHealth").mockResolvedValue({
      ok: true,
      store_kind: "memory",
      provider: {
        provider_name: "openrouter",
        provider_kind: "openai_compatible",
        healthy: true,
        configured: true,
        latency_ms: 48,
        avg_latency_ms: 48,
        request_count: 2,
        error_count: 0,
      },
    });

    renderHeader();

    expect(await screen.findByRole("status", { name: /Provider status: openrouter/i })).toBeInTheDocument();
  });

  it("shows offline provider status when health fails", async () => {
    mockModelQueries();
    vi.spyOn(api, "fetchHealth").mockRejectedValue(new Error("network"));

    renderHeader();

    expect(await screen.findByRole("status", { name: /Provider status: offline/i })).toBeInTheDocument();
  });

  it("shows compact run context when a run is active", async () => {
    mockModelQueries();
    vi.spyOn(api, "fetchHealth").mockResolvedValue({
      ok: true,
      store_kind: "memory",
      provider: {
        provider_name: "openrouter",
        provider_kind: "openai_compatible",
        healthy: true,
        configured: true,
        latency_ms: 48,
        avg_latency_ms: 48,
        request_count: 2,
        error_count: 0,
      },
    });
    useChatStore.setState({
      runId: "run_1234567890abcdef",
      messages: [
        {
          id: "a1",
          role: "assistant",
          content: "done",
          runId: "run_1234567890abcdef",
        },
      ],
    });

    renderHeader();

    expect(screen.getByText("run 1")).toBeInTheDocument();
    expect(screen.getByText("run_1234...cdef")).toBeInTheDocument();
  });
});
