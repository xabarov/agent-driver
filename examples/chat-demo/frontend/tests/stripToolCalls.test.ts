import { describe, expect, test } from "vitest";

import { stripTextFormToolCalls } from "../src/lib/stripToolCalls";

describe("stripTextFormToolCalls", () => {
  test("removes tool_call block from text", () => {
    const raw =
      'Summary.\n<tool_call>{"name":"web_search","arguments":{"query":"test"}}</tool_call>';
    expect(stripTextFormToolCalls(raw)).toBe("Summary.");
  });
});
