import { describe, expect, test } from "vitest";

import { stripTextFormToolCalls } from "../src/lib/stripToolCalls";

describe("stripTextFormToolCalls", () => {
  test("removes tool_call block from text", () => {
    const raw =
      'Summary.\n<tool_call>{"name":"web_search","arguments":{"query":"test"}}</tool_call>';
    expect(stripTextFormToolCalls(raw)).toBe("Summary.");
  });

  test("removes tool_call blocks with spaced closing tag", () => {
    const raw =
      'Before\n<tool_call>{"name":"todo_write","arguments":{}}</ tool_call>\nAfter';
    expect(stripTextFormToolCalls(raw)).toBe("Before\nAfter");
  });

  test("hides trailing partial tool_call block while streaming", () => {
    const raw = 'Visible text\n<tool_call>{"name":"todo_write"';
    expect(stripTextFormToolCalls(raw)).toBe("Visible text");
  });

  test("removes orphan tool call json with only a closing tag", () => {
    const raw =
      'Before\n{"name":"todo_update","arguments":{"todo_id":"research","status":"in_progress"}} </tool_call>\nAfter';
    expect(stripTextFormToolCalls(raw)).toBe("Before\nAfter");
  });
});
