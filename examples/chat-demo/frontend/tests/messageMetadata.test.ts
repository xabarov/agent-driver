import { describe, expect, test } from "vitest";

import {
  formatCostUsd,
  formatDurationSec,
  formatTokenCount,
  formatTokensPerSecond,
  mergeAssistantMetadata,
  parseLlmCompletedData,
} from "../src/lib/messageMetadata";

describe("messageMetadata", () => {
  test("parseLlmCompletedData reads usage and duration", () => {
    const patch = parseLlmCompletedData({
      provider: "openrouter",
      model: "test/model",
      duration_ms: 5300,
      usage: {
        input_tokens: 100,
        output_tokens: 678,
        total_tokens: 778,
        cost_usd_estimate: 0.0225145,
        model_provider: "openrouter",
        model_name: "test/model",
      },
    });
    expect(patch.promptTokens).toBe(100);
    expect(patch.completionTokens).toBe(678);
    expect(patch.totalTokens).toBe(778);
    expect(patch.costUsd).toBeCloseTo(0.0225145);
    expect(patch.durationMs).toBe(5300);
    expect(patch.provider).toBe("openrouter");
  });

  test("mergeAssistantMetadata aggregates multiple LLM steps", () => {
    const first = parseLlmCompletedData({
      duration_ms: 2000,
      usage: { input_tokens: 50, output_tokens: 100, total_tokens: 150, cost_usd_estimate: 0.01 },
    });
    const second = parseLlmCompletedData({
      duration_ms: 3000,
      usage: { input_tokens: 20, output_tokens: 80, total_tokens: 100, cost_usd_estimate: 0.005 },
    });
    const merged = mergeAssistantMetadata(
      mergeAssistantMetadata(undefined, first),
      second,
    );
    expect(merged.promptTokens).toBe(70);
    expect(merged.completionTokens).toBe(180);
    expect(merged.totalTokens).toBe(250);
    expect(merged.durationMs).toBe(5000);
    expect(merged.costUsd).toBeCloseTo(0.015);
    expect(merged.tokensPerSecond).toBeCloseTo(36, 0);
  });

  test("formatters match OpenRouter-style labels", () => {
    expect(formatTokensPerSecond(147.8)).toBe("~147.8 tokens/s");
    expect(formatTokenCount({ totalTokens: 778 })).toBe("778 tokens");
    expect(formatCostUsd(0.0225145)).toBe("$0.0225145");
    expect(formatDurationSec(5300)).toBe("5.3s");
  });
});
