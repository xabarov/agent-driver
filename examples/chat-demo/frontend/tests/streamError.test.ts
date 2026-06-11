import { describe, expect, test } from "vitest";

import { formatRunFailure } from "../src/lib/streamError";

describe("formatRunFailure", () => {
  test("explains OpenRouter payment/model availability failures", () => {
    expect(
      formatRunFailure({
        reason: "model_error",
        status_code: 402,
        message: "Insufficient credits for this model.",
      }),
    ).toContain("HTTP 402");
    expect(
      formatRunFailure({
        reason: "model_error",
        status_code: 402,
        message: "Insufficient credits for this model.",
      }),
    ).toContain("Insufficient credits");
  });
});
