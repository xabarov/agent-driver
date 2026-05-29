import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ModelPicker } from "../src/components/layout/ModelPicker";
import * as api from "../src/lib/api";
import { useSettingsStore } from "../src/store/settingsStore";

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function mockApi() {
  vi.spyOn(api, "fetchProviders").mockResolvedValue({
    name: "openrouter",
    model: "openai/gpt-4.1-mini",
    base_url: "https://openrouter.ai/api/v1",
    status: {
      provider_name: "openrouter",
      provider_kind: "openai_compatible",
      healthy: true,
      configured: true,
      latency_ms: 42,
      avg_latency_ms: 42,
      request_count: 1,
      error_count: 0,
    },
  });
  vi.spyOn(api, "fetchModels").mockResolvedValue({
    provider: "openrouter",
    models: [
      {
        id: "openai/gpt-4.1-mini",
        name: "GPT 4.1 Mini",
        description: null,
        context_length: 128000,
      },
      {
        id: "anthropic/claude-sonnet-4.5",
        name: "Claude Sonnet 4.5",
        description: null,
        context_length: 200000,
      },
      {
        id: "qwen/qwen3-235b-a22b-2507",
        name: null,
        description: null,
        context_length: null,
      },
    ],
  });
}

async function openPicker(name: RegExp) {
  const trigger = await screen.findByRole("button", { name });
  fireEvent.pointerDown(trigger);
  fireEvent.click(trigger);
}

afterEach(() => {
  cleanup();
  localStorage.clear();
  useSettingsStore.setState({ model: "", toolPreset: "web" });
  vi.restoreAllMocks();
});

describe("ModelPicker", () => {
  it("shows selected/default, provider context, and model ids", async () => {
    mockApi();
    renderWithClient(<ModelPicker />);

    await openPicker(/openai\/gpt-4.1-mini/i);

    expect(await screen.findByText("Selected")).toBeInTheDocument();
    expect(screen.getByText("openrouter")).toBeInTheDocument();
    expect(screen.getByText("GPT 4.1 Mini")).toBeInTheDocument();
    expect(screen.getAllByText("openai/gpt-4.1-mini").length).toBeGreaterThan(0);
  });

  it("filters models by display name and selects a model", async () => {
    mockApi();
    renderWithClient(<ModelPicker />);

    await openPicker(/openai\/gpt-4.1-mini/i);
    fireEvent.change(screen.getByPlaceholderText(/Search models/i), {
      target: { value: "sonnet" },
    });
    fireEvent.click(await screen.findByText("Claude Sonnet 4.5"));

    await waitFor(() => {
      expect(useSettingsStore.getState().model).toBe("anthropic/claude-sonnet-4.5");
    });
  });

  it("shows recent models after a selection", async () => {
    mockApi();
    useSettingsStore.setState({ model: "anthropic/claude-sonnet-4.5" });
    localStorage.setItem("chat-demo-recent-models", JSON.stringify(["openai/gpt-4.1-mini"]));

    renderWithClient(<ModelPicker />);
    await openPicker(/anthropic\/claude-sonnet-4.5/i);

    expect(await screen.findByText("Recent")).toBeInTheDocument();
    expect(screen.getByText("GPT 4.1 Mini")).toBeInTheDocument();
  });
});
